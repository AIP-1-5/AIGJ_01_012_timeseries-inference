"""과제 8 상시 추론 서비스.

  python -m service export              보드 B: 학습 결과를 data/models/로 묶기 (ONNX 변환 포함)
  python -m service once                한 주기만 계산해 JSON 출력 (배포 확인용)
  python -m service run                 매분 계산 + HTTP 조회 API + MQTT 발행 (보드 A 상시 실행)
  python -m service summary --hours 12  주기 기록(data/service/cycles.csv) → docs/reports/008-service-log.md
"""

import argparse
import csv
import json
import logging
import os
import signal
import socket
import getpass
import sys
import threading
import time
import tomllib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("service")

DASHBOARD = Path(__file__).with_name("dashboard.html")
CYCLE_FIELDS = ["time", "ok", "ms", "raw_rows", "n_spaces", "alerts", "data_gaps", "error"]


def load_service_config(path):
    with Path(path).open("rb") as fp:
        return tomllib.load(fp)


# ---------- 상태 보관 (계산 스레드가 쓰고 HTTP 스레드가 읽는다) ----------

class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.latest = None              # 마지막 성공 주기 결과
        self.last_error = None
        self.last_ok_at = None
        self.started_at = time.time()
        self.cycles = 0
        self.failures = 0
        self.alert_log = []             # 최근 발송한 경보 (최대 200)

    def snapshot(self):
        with self.lock:
            return self.latest, self.last_error, self.last_ok_at


class AlertGate:
    """같은 (공간, 종류) 경보는 처음 생길 때, 그리고 repeat_minutes마다만 보낸다. 사라지면 '해제'를 한 번 보낸다."""

    def __init__(self, repeat_minutes):
        self.repeat = repeat_minutes * 60
        self.active = {}                # (slug, type) → 마지막 발송 시각

    def step(self, result, now):
        out, seen = [], set()
        for slug, entry in result["spaces"].items():
            for alert in entry["alerts"]:
                key = (slug, alert["type"])
                seen.add(key)
                last = self.active.get(key)
                if last is None or now - last >= self.repeat:
                    self.active[key] = now
                    out.append({**alert, "space": entry["space"], "slug": slug, "status": "active" if last is None else "repeat",
                                "time": result["generated_at"]})
        for key in [k for k in self.active if k not in seen]:
            del self.active[key]
            out.append({"type": key[1], "slug": key[0], "space": result["spaces"].get(key[0], {}).get("space", key[0]),
                        "status": "cleared", "level": "info", "message": "해제", "time": result["generated_at"]})
        return out


# ---------- MQTT 발행 ----------

class Publisher:
    def __init__(self, cfg, broker):
        import paho.mqtt.client as mqtt
        from paho.mqtt.enums import CallbackAPIVersion

        self.prefix = cfg["topic_prefix"].rstrip("/")
        self.qos = int(cfg.get("qos", 1))
        username, password = broker.credentials()
        client_id = f"{socket.gethostname()}-{getpass.getuser()}-inference"     # 수집기와 겹치지 않게
        self.client = mqtt.Client(callback_api_version=CallbackAPIVersion.VERSION2, client_id=client_id,
                                  protocol=mqtt.MQTTv311)
        self.client.username_pw_set(username, password)
        self.client.will_set(f"{self.prefix}/status", json.dumps({"status": "offline"}), qos=self.qos, retain=True)
        self.client.reconnect_delay_set(1, 30)
        self.client.on_connect = lambda c, u, f, rc, p: (log.info("mqtt connected rc=%s", rc), self.status("online"))
        self.client.connect_async(broker.host, broker.port, keepalive=30)
        self.client.loop_start()

    def publish(self, topic, payload, retain=False):
        self.client.publish(f"{self.prefix}/{topic}", json.dumps(payload, ensure_ascii=False), qos=self.qos, retain=retain)

    def status(self, status, **extra):
        self.publish("status", {"status": status, **extra}, retain=True)

    def close(self):
        self.status("offline")
        time.sleep(0.5)
        self.client.loop_stop()
        self.client.disconnect()


# ---------- HTTP 조회 API ----------

def make_handler(state, stale_after):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):
            log.debug("http " + fmt, *args)

        def send(self, code, body):
            data = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            parts = [p for p in url.path.split("/") if p]
            if parts in ([], ["dashboard"]):
                data = DASHBOARD.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            latest, error, ok_at = state.snapshot()
            age = None if ok_at is None else round(time.time() - ok_at, 1)
            if parts == ["health"]:
                healthy = age is not None and age <= stale_after
                return self.send(200 if healthy else 503, {
                    "status": "ok" if healthy else "stale", "last_cycle_age_seconds": age, "cycles": state.cycles,
                    "failures": state.failures, "uptime_seconds": round(time.time() - state.started_at),
                    "last_error": error, "models": (latest or {}).get("models")})
            if latest is None:
                return self.send(503, {"error": "아직 첫 계산 전", "last_error": error})
            if parts == ["v1", "spaces"]:
                return self.send(200, {"generated_at": latest["generated_at"], "slot": latest["slot"], "stale": age > stale_after,
                                       "spaces": list(latest["spaces"].values())})
            if len(parts) >= 3 and parts[:2] == ["v1", "spaces"]:
                entry = latest["spaces"].get(parts[2])
                if entry is None:
                    return self.send(404, {"error": f"없는 공간: {parts[2]}", "spaces": list(latest["spaces"])})
                if len(parts) == 3:
                    return self.send(200, entry)
                if parts[3:] == ["forecast"]:
                    minutes = parse_qs(url.query).get("minutes", [None])[0]
                    fc = entry.get("forecast") or {}
                    if minutes is None:
                        return self.send(200, {"space": entry["space"], "slot": entry["slot"], "forecast": fc,
                                               "unavailable": entry.get("forecast_unavailable")})
                    try:
                        m = int(minutes)
                    except ValueError:
                        return self.send(400, {"error": "minutes는 5~60 사이 5의 배수"})
                    if m % 5 or not 5 <= m <= 60:
                        return self.send(400, {"error": "minutes는 5~60 사이 5의 배수"})
                    return self.send(200, {"space": entry["space"], "slot": entry["slot"], "minutes": m,
                                           **{k: next(p["value"] for p in v["points"] if p["minutes"] == m) for k, v in fc.items()},
                                           "unavailable": entry.get("forecast_unavailable")})
                for key in ("anomaly", "state", "alerts"):
                    if parts[3:] == [key]:
                        return self.send(200, {"space": entry["space"], "slot": entry["slot"], key: entry.get(key)})
            if parts == ["v1", "alerts"]:
                with state.lock:
                    recent = list(state.alert_log[-100:])
                active = [{**a, "space": e["space"], "slug": s} for s, e in latest["spaces"].items() for a in e["alerts"]]
                return self.send(200, {"generated_at": latest["generated_at"], "active": active, "recent": recent})
            return self.send(404, {"error": "없는 주소", "routes": ["/health", "/v1/spaces", "/v1/spaces/<slug>",
                                                                  "/v1/spaces/<slug>/forecast?minutes=60", "/v1/spaces/<slug>/anomaly",
                                                                  "/v1/spaces/<slug>/state", "/v1/alerts"]})

    return Handler


# ---------- 주기 기록 ----------

def append_cycle(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fp:
        w = csv.DictWriter(fp, fieldnames=CYCLE_FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)


def build_engine(root, cfg):
    from collector.config import load_config
    from pipeline.settings import load_schedule, load_settings
    from service.engine import Engine, Models

    settings = load_settings(root / "config" / "preprocess.toml", root=root)
    schedule = load_schedule(root / "config" / "schedule.toml")
    collector_cfg = load_config(root / cfg["data"]["collector_config"])
    models = Models(root / cfg["models"]["dir"])
    return Engine(settings, schedule, collector_cfg, cfg, models), collector_cfg


def run_one(engine, state, gate, publisher, cycles_path, now=None):
    """한 주기. 실패해도 예외를 밖으로 내보내지 않는다 (다음 주기에 다시 시도)."""
    t0 = time.time()
    try:
        result = engine.run_cycle(now)
    except Exception as exc:                                       # noqa: BLE001 — 어떤 실패든 서비스는 계속
        log.exception("cycle failed")
        with state.lock:
            state.last_error = f"{type(exc).__name__}: {exc}"
            state.failures += 1
            state.cycles += 1
        append_cycle(cycles_path, {"time": pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%SZ"), "ok": 0,
                                   "ms": round((time.time() - t0) * 1000, 1), "raw_rows": "", "n_spaces": 0, "alerts": "",
                                   "data_gaps": "", "error": state.last_error[:200]})
        if publisher:
            publisher.status("degraded", error=state.last_error[:200])
        return None
    sent = gate.step(result, time.time())
    with state.lock:
        state.latest, state.last_error, state.last_ok_at = result, None, time.time()
        state.cycles += 1
        state.alert_log = (state.alert_log + sent)[-200:]
    all_alerts = [a["type"] for e in result["spaces"].values() for a in e["alerts"]]
    append_cycle(cycles_path, {"time": result["generated_at"], "ok": 1, "ms": result["cycle_ms"], "raw_rows": result["raw_rows"],
                               "n_spaces": len(result["spaces"]), "alerts": ";".join(f"{s}:{a['type']}" for s, e in result["spaces"].items() for a in e["alerts"]),
                               "data_gaps": sum(t == "data_gap" for t in all_alerts), "error": ""})
    for a in sent:
        log.info("alert %s %s %s", a["status"], a["slug"], a["type"])
    if publisher:
        for slug, entry in result["spaces"].items():
            publisher.publish(f"{slug}/state", entry, retain=True)
        for a in sent:
            publisher.publish("alerts", a)
        publisher.status("online", generated_at=result["generated_at"], cycle_ms=result["cycle_ms"])
    return result


class ModelWatcher:
    """manifest.json이 바뀌고 파일 해시가 모두 맞으면 새 모델을 불러와 바꿔 끼운다 (서비스를 멈추지 않는 모델 교체)."""

    def __init__(self, engine):
        self.engine = engine
        self.path = engine.models.dir / "manifest.json"
        self.seen = self.stamp()

    def stamp(self):
        try:
            return self.path.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def check(self):
        from service.engine import Models
        from service.export import sha256

        stamp = self.stamp()
        if stamp is None or stamp == self.seen:
            return False
        try:
            manifest = json.loads(self.path.read_text(encoding="utf-8"))
            bad = [n for n, h in manifest["files"].items() if sha256(self.path.parent / n) != h]
        except (OSError, ValueError, KeyError) as exc:          # 복사 중이면 다음 주기에 다시
            log.info("models changing (%s), retry next cycle", exc)
            return False
        if bad:
            log.info("models changing (%s not matching), retry next cycle", ", ".join(bad))
            return False
        try:
            models = Models(self.path.parent)
        except Exception:                                      # noqa: BLE001 — 새 모델이 깨졌으면 기존 모델로 계속
            log.exception("new models failed to load, keeping %s", self.engine.models.summary())
            self.seen = stamp
            return False
        old = self.engine.models.summary()
        self.engine.models = models
        self.engine.pending.clear()
        self.engine.scored.clear()
        self.seen = stamp
        log.info("models reloaded: %s -> %s", old, models.summary())
        return True


def cmd_run(root, cfg, args):
    engine, collector_cfg = build_engine(root, cfg)
    state = State()
    gate = AlertGate(float(cfg["cycle"].get("alert_repeat_minutes", 30)))
    period = float(cfg["cycle"].get("seconds", 60))
    publisher = None
    if cfg["mqtt"].get("enabled", True) and not args.no_mqtt:
        try:
            publisher = Publisher(cfg["mqtt"], collector_cfg.broker)
        except Exception:                                          # noqa: BLE001 — MQTT 없이도 API는 동작
            log.exception("mqtt disabled")
    host, port = cfg["http"].get("host", "0.0.0.0"), int(args.port or cfg["http"].get("port", 8080))
    server = ThreadingHTTPServer((host, port), make_handler(state, stale_after=3 * period))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("http on %s:%s, models %s", host, port, engine.models.manifest.get("forecast_run"))

    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    cycles_path = root / "data" / "service" / "cycles.csv"
    watcher = ModelWatcher(engine)
    while not stop.is_set():
        began = time.time()
        watcher.check()
        result = run_one(engine, state, gate, publisher, cycles_path)
        if result:
            log.info("cycle ok %.0f ms, rows=%s, alerts=%d", result["cycle_ms"], result["raw_rows"],
                     sum(len(e["alerts"]) for e in result["spaces"].values()))
        stop.wait(max(1.0, period - (time.time() - began)))
    server.shutdown()
    if publisher:
        publisher.close()
    return 0


# ---------- 12시간 요약 ----------

def summarize_cycles(frame, period_seconds):
    frame = frame.sort_values("time")
    ok = frame[frame["ok"] == 1]
    gaps = frame["time"].diff().dt.total_seconds().fillna(0)
    down = gaps[gaps > 3 * period_seconds]
    span = (frame["time"].iloc[-1] - frame["time"].iloc[0]).total_seconds() if len(frame) else 0
    expected = span / period_seconds + 1 if len(frame) else 0
    alerts = ok["alerts"].fillna("").str.split(";").explode()
    alerts = alerts[alerts != ""]
    return {
        "start": frame["time"].iloc[0] if len(frame) else None, "end": frame["time"].iloc[-1] if len(frame) else None,
        "hours": span / 3600, "cycles": len(frame), "ok": len(ok), "failed": int((frame["ok"] == 0).sum()),
        "expected": expected, "coverage": len(ok) / expected if expected else 0,
        "ms_p50": ok["ms"].median(), "ms_p95": ok["ms"].quantile(0.95), "ms_max": ok["ms"].max(),
        "longest_gap_s": gaps.max() if len(gaps) else 0,
        "downtimes": [(t - pd.Timedelta(seconds=g), t, g) for t, g in zip(frame.loc[down.index, "time"], down)],
        "alert_counts": alerts.value_counts().to_dict(),
        "errors": frame.loc[frame["ok"] == 0, "error"].value_counts().head(5).to_dict(),
    }


SCORED = {"ventilation": 30, "co2_forecast": 60}     # 경보 종류 → 실제 1,000 ppm 도달을 확인할 시간(분)


def score_alerts(root, cfg, frame, merge_minutes=30):
    """운영 중 낸 CO₂ 경보가 실제로 맞았는지: 경보 이벤트마다 그 뒤 창 안에 공간 CO₂(5분 중앙값)가 1,000 ppm에 닿았나."""
    from anomaly.data import METRICS, space_frames
    from pipeline.loader import from_collector_db
    from pipeline.preprocess import preprocess
    from pipeline.settings import load_settings

    settings = load_settings(root / "config" / "preprocess.toml", root=root)
    slug_to_space = {spec.slug: spec.space for spec in settings.spaces}
    events = []
    rows = frame[frame["ok"] == 1].sort_values("time")
    for kind in SCORED:
        last = {}
        for t, alerts in zip(rows["time"], rows["alerts"].fillna("")):
            for item in alerts.split(";"):
                if ":" not in item:
                    continue
                slug, typ = item.split(":", 1)
                if typ != kind:
                    continue
                if slug not in last or (t - last[slug]).total_seconds() > merge_minutes * 60:
                    events.append({"slug": slug, "type": kind, "time": t})
                last[slug] = t
    if not events:
        return []
    start = min(e["time"] for e in events) - pd.Timedelta(hours=2)
    end = max(e["time"] for e in events) + pd.Timedelta(minutes=max(SCORED.values()) + 5)
    raw = from_collector_db(root / cfg["data"]["collector_db"], start, end, list(METRICS))
    clean, _, _ = preprocess({m: raw[raw["metric"].astype(str) == m] for m in METRICS}, settings)
    frames = space_frames(clean, settings)
    out = []
    for e in events:
        co2 = frames.get(slug_to_space.get(e["slug"], e["slug"]), pd.DataFrame()).get("co2")
        if co2 is None:
            continue
        window = co2[(co2.index > e["time"] - pd.Timedelta(minutes=5)) & (co2.index <= e["time"] + pd.Timedelta(minutes=SCORED[e["type"]]))]
        if window.dropna().empty or co2.index.max() < e["time"] + pd.Timedelta(minutes=SCORED[e["type"]]):
            continue                                       # 아직 창이 다 지나지 않음
        out.append({**e, "max_co2": float(window.max()), "hit": bool(window.max() >= 1000)})
    return out


def cmd_summary(root, cfg, args):
    path = root / "data" / "service" / "cycles.csv"
    frame = pd.read_csv(path)
    frame["time"] = pd.to_datetime(frame["time"], utc=True)
    if args.since:
        frame = frame[frame["time"] >= pd.Timestamp(args.since, tz="Asia/Seoul").tz_convert("UTC")]
    if args.hours:
        frame = frame[frame["time"] >= frame["time"].max() - pd.Timedelta(hours=args.hours)]
    if frame.empty:
        print("기록 없음", file=sys.stderr)
        return 1
    period = float(cfg["cycle"].get("seconds", 60))
    s = summarize_cycles(frame, period)
    kst = lambda t: t.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M")   # noqa: E731
    lines = [
        "# 과제 8 상시 서비스 운영 기록 (자동 생성)", "",
        f"- 생성: `python -m service summary` / 기록 파일 `data/service/cycles.csv` / 보드 `{socket.gethostname()}`",
        f"- 기간: {kst(s['start'])} ~ {kst(s['end'])} KST ({s['hours']:.2f}시간), 주기 {period:.0f}초", "",
        "| 항목 | 값 |", "| --- | --- |",
        f"| 주기 수 (성공 / 실패) | {s['cycles']} ({s['ok']} / {s['failed']}) |",
        f"| 기대 주기 수 대비 성공 | {s['coverage']:.1%} ({s['ok']} / {s['expected']:.0f}) |",
        f"| 계산 시간 p50 / p95 / 최대 | {s['ms_p50']:.0f} / {s['ms_p95']:.0f} / {s['ms_max']:.0f} ms |",
        f"| 가장 긴 기록 간격 | {s['longest_gap_s']:.0f}초 |",
        f"| 멈춘 구간 (간격 > {3 * period:.0f}초) | {len(s['downtimes'])}회 |", "",
    ]
    if s["downtimes"]:
        lines += ["| 멈춤 시작 | 다시 시작 | 길이 |", "| --- | --- | --- |"]
        lines += [f"| {kst(a)} | {kst(b)} | {g / 60:.1f}분 |" for a, b, g in s["downtimes"]] + [""]
    lines += ["## 경보 (주기 × 공간 기준 횟수)", "", "| 공간:종류 | 주기 수 |", "| --- | --- |"]
    lines += [f"| {k} | {v} |" for k, v in sorted(s["alert_counts"].items())] or ["| (없음) | 0 |"]
    if args.score:
        scored = score_alerts(root, cfg, frame)
        lines += ["", "## 경보 채점 (실제 CO₂로 확인)", "",
                  "같은 공간·종류의 경보는 30분 안이면 한 이벤트. 환기 경보는 30분, CO₂ 초과 예측은 60분 안에 공간 CO₂(5분 중앙값)가 1,000 ppm에 닿으면 맞음.", "",
                  "| 공간 | 종류 | 경보 시각 (KST) | 창 안 최대 CO₂ | 맞음 |", "| --- | --- | --- | ---: | --- |"]
        lines += [f"| {e['slug']} | {e['type']} | {kst(e['time'])} | {e['max_co2']:.0f} | {'O' if e['hit'] else 'X'} |" for e in scored] or ["| (채점할 경보 없음) | | | | |"]
        for kind in SCORED:
            k = [e for e in scored if e["type"] == kind]
            if k:
                lines.append(f"\n- `{kind}`: {sum(e['hit'] for e in k)}/{len(k)} 맞음 ({sum(e['hit'] for e in k) / len(k):.0%})")
    if s["errors"]:
        lines += ["", "## 실패 원인", "", "| 오류 | 횟수 |", "| --- | --- |"]
        lines += [f"| `{k}` | {v} |" for k, v in s["errors"].items()]
    out = root / "docs" / "reports" / "008-service-log.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m service")
    parser.add_argument("--config", default=str(ROOT / "config" / "service.toml"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_exp = sub.add_parser("export")
    p_exp.add_argument("--run", help="select 전 중간 배포용 실행 이름 (예: linear-full-L120-s1)")
    sub.add_parser("once")
    p_run = sub.add_parser("run")
    p_run.add_argument("--port", type=int)
    p_run.add_argument("--no-mqtt", action="store_true")
    p_sum = sub.add_parser("summary")
    p_sum.add_argument("--hours", type=float)
    p_sum.add_argument("--since", help="KST 시작 시각 (예: 2026-10-22T15:00)")
    p_sum.add_argument("--score", action="store_true", help="CO₂ 경보를 수집기 DB의 실제 값으로 채점")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    config_path = Path(args.config)
    root = config_path.resolve().parent.parent
    cfg = load_service_config(config_path)
    if args.cmd == "export":
        from service.export import export

        manifest = export(root, root / cfg["models"]["dir"], run=args.run)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "once":
        engine, _ = build_engine(root, cfg)
        print(json.dumps(engine.run_cycle(), ensure_ascii=False, indent=1))
        return 0
    if args.cmd == "run":
        return cmd_run(root, cfg, args)
    return cmd_summary(root, cfg, args)


if __name__ == "__main__":
    sys.exit(main())
