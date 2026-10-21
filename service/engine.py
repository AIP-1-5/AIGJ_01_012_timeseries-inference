"""매분 계산 (과제 8 해결할 것 1·3). 학습 때와 같은 전처리·특성 함수를 그대로 부른다.

1. 수집기 DB에서 최근 history_hours를 읽는다 (막 시작된 5분 칸은 버리고, 끝난 칸만 쓴다)
2. pipeline.preprocess로 정제·5분 칸·공간 중앙값 (과제 2와 같은 함수)
3. 공간마다: 60분 예측(과제 3 모델), 이상 점수·규칙(과제 4), 사용 여부·환기 경보(과제 5), 데이터 끊김
"""

import json
import pickle
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from anomaly import rules as rule_module
from anomaly.data import METRICS, space_frames
from anomaly.model import TAIL, window_features
from forecast.dataset import space_arrays
from forecast.features import calendar_features
from pipeline.loader import from_collector_db
from pipeline.preprocess import preprocess
from state.features import calendar_frame, sensor_features

CO2_LIMIT = 1000.0


def now_utc():
    return pd.Timestamp.now(tz="UTC")


class Models:
    def __init__(self, models_dir):
        import onnxruntime as ort

        self.dir = Path(models_dir)
        self.forecast_meta = json.loads((self.dir / "forecast.json").read_text(encoding="utf-8"))
        self.anomaly_meta = json.loads((self.dir / "anomaly.json").read_text(encoding="utf-8"))
        self.normalization = json.loads((self.dir / "normalization.json").read_text(encoding="utf-8"))
        self.manifest = json.loads((self.dir / "manifest.json").read_text(encoding="utf-8"))
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2                     # 같은 보드의 다른 모델(LLM 등)을 위해 적게
        self.forecast = ort.InferenceSession(str(self.dir / "forecast.onnx"), options, providers=["CPUExecutionProvider"])
        self.autoencoder = ort.InferenceSession(str(self.dir / "autoencoder.onnx"), options, providers=["CPUExecutionProvider"])
        import sklearn

        made_with = self.manifest.get("versions", {}).get("scikit-learn")
        if made_with and made_with != sklearn.__version__:     # 피클은 같은 버전에서만 결과가 같다고 보장된다
            raise RuntimeError(f"state.pkl은 scikit-learn {made_with}로 만들었다. 지금 {sklearn.__version__} → "
                               f"pip install scikit-learn=={made_with}")
        with (self.dir / "state.pkl").open("rb") as fp:
            self.state = pickle.load(fp)


class Engine:
    def __init__(self, settings, schedule, collector_cfg, cfg, models):
        self.settings, self.schedule, self.collector_cfg, self.cfg, self.models = settings, schedule, collector_cfg, cfg, models
        self.db = settings.root / cfg["data"]["collector_db"]
        self.spaces = [s.space for s in settings.spaces]
        self.pending = {}            # (slug, 항목, 예측 대상 시각) → 60분 전에 낸 예측값 (자체 채점용, 메모리에만)
        self.scored = {}             # (slug, 항목) → [(대상 시각, 절대 오차)]

    # ---------- 데이터 ----------

    def read_raw(self, now):
        start = now - pd.Timedelta(hours=float(self.cfg["data"].get("history_hours", 26)))
        last_label = now.floor(f"{self.settings.interval_minutes}min")
        raw = from_collector_db(self.db, start, last_label, list(METRICS))     # 끝난 칸까지만 ([t−5, t) 칸이 다 찬 것)
        return raw, last_label

    def sensor_freshness(self, now):
        """센서별 마지막 수신 시각과 끊김 여부 (기대 간격 × stale_factor, 최소 stale_min_minutes)."""
        uri = f"file:{self.db.resolve()}?mode=ro"
        since = (now - pd.Timedelta(hours=float(self.cfg["data"].get("history_hours", 26)))).strftime("%Y-%m-%dT%H:%M:%S")
        with sqlite3.connect(uri, uri=True) as conn:
            rows = conn.execute("SELECT device_id, device_profile, space, MAX(measured_at) FROM measurements "
                                "WHERE measured_at >= ? GROUP BY device_id", (since,)).fetchall()
        factor = float(self.cfg["data"].get("stale_factor", 3))
        floor_min = float(self.cfg["data"].get("stale_min_minutes", 15))
        out = []
        for device, profile, space, last in rows:
            expected = self.collector_cfg.intervals.minutes_for(device, profile)
            age = (now - pd.Timestamp(last)).total_seconds() / 60
            limit = max(expected * factor, floor_min)
            out.append({"device_id": device, "space": space, "profile": profile, "last_seen": last,
                        "age_minutes": round(age, 1), "stale": age > limit, "limit_minutes": limit})
        return out

    # ---------- 공간별 계산 ----------

    def forecast(self, clean, space, last_label, frame):
        meta = self.models.forecast_meta
        arrays = space_arrays(clean, space, self.models.normalization, self.settings, meta["variant"], self.schedule)
        if arrays is None:
            return None, "데이터 없음"
        grid, feats, cal, z, y, observed, usable, mean, std = arrays
        if last_label not in grid:
            return None, "최근 칸 없음"
        p = grid.get_loc(last_label)
        L = meta["lookback_steps"]
        if p < L - 1:
            return None, f"과거 {L}칸이 아직 없음"
        window_ok = usable[p - L + 1:p + 1].mean(axis=0)
        if not (window_ok >= 0.75).any():
            return None, "과거 창의 75% 미만만 값이 있음"
        past = feats[None, p - L + 1:p + 1].astype(np.float32)
        future_times = pd.date_range(last_label + pd.Timedelta(minutes=5), periods=12, freq="5min")
        future = calendar_features(future_times, space, meta["variant"], self.schedule, self.settings.timezone)[None].astype(np.float32)
        onehot = np.zeros((1, len(meta["spaces"])), np.float32)
        onehot[0, meta["spaces"].index(space)] = 1
        delta = self.models.forecast.run(None, {"past": past, "future": future, "space": onehot})[0][0]
        result = {}
        fresh = pd.Timedelta(minutes=float(self.cfg["data"].get("stale_min_minutes", 15)))
        for j, metric in enumerate(("co2", "temperature")):
            now_value = frame[metric].loc[:last_label].dropna()
            if now_value.empty or window_ok[j] < 0.75 or metric not in self.models.normalization["values"].get(space, {}):
                continue
            if last_label - now_value.index[-1] > fresh:          # 최근 값이 끊겼으면 예측하지 않는다 (오래된 값으로 '지금'을 대신하지 않음)
                continue
            base = float(now_value.iloc[-1])
            result[metric] = {"now": round(base, 2), "as_of": now_value.index[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
                              "points": [{"minutes": 5 * (k + 1), "value": round(base + float(delta[k, j]) * float(std[j]), 2)}
                                         for k in range(12)]}
        return result, None if result else "최근 값이 끊김"

    def anomaly(self, frame, space, last_label):
        meta = self.models.anomaly_meta
        values, mask, cond, valid = window_features(frame, space, meta["spaces"], self.models.normalization, self.schedule,
                                                    meta["window"], self.settings.timezone)
        p = len(frame) - 1
        if frame.index[p] != last_label or p not in set(valid.tolist()):
            return {"score": None, "is_anomaly": None, "reason": "최근 1시간 창이 비어 있음"}
        idx = np.arange(p - meta["window"] + 1, p + 1)
        x, m, c = values[None, idx], mask[None, idx].astype(np.float32), cond[None, p]
        recon = self.models.autoencoder.run(None, {"x": x, "cond": c})[0]
        err = np.abs(recon - x) * m
        per = (err[:, -TAIL:, :3] + err[:, -TAIL:, 3:]).sum(axis=1) / np.clip((m[:, -TAIL:, :3] + m[:, -TAIL:, 3:]).sum(axis=1), 1, None)
        present = m[:, -TAIL:, :3].sum(axis=1) > 0
        score = float((per * present).sum() / max(present.sum(), 1))
        cause = METRICS[int(np.where(present[0], per[0], -1).argmax())]
        threshold = float(meta["threshold"])
        return {"score": round(score, 4), "threshold": threshold, "is_anomaly": score > threshold,
                "cause": cause if score > threshold else None,
                "by_metric": {METRICS[i]: round(float(per[0, i]), 4) for i in range(3) if present[0, i]}}

    def rule_alarms(self, frame, space, last_label):
        rows = rule_module.apply({space: frame}, self.models.anomaly_meta["rules"], self.schedule, self.settings.timezone)
        recent = rows[rows["time"] >= last_label - pd.Timedelta(minutes=5)]
        return [{"rule": r.rule, "cause": r.cause, "time": r.time.strftime("%Y-%m-%dT%H:%M:%SZ")}
                for r in recent.drop_duplicates(["rule", "cause"]).itertuples()]

    def state(self, frame, space, last_label):
        st = self.models.state
        feats = sensor_features(frame)
        cal = calendar_frame(frame.index, space, self.schedule, self.settings.timezone)
        row = pd.concat([feats, cal], axis=1).loc[[last_label]]
        if row[["co2", "temperature"]].isna().all(axis=1).iloc[0]:
            return {"occupied": None, "reason": "센서값 없음"}
        p_occ = float(st["occupancy"].predict_proba(row[st["occupancy_features"]])[0, 1])
        out = {"occupied": p_occ >= st["occupancy_threshold"], "occupied_probability": round(p_occ, 3)}
        if not np.isnan(row["co2"].iloc[0]):
            p_vent = float(st["ventilation"].predict_proba(row[st["ventilation_features"]])[0, 1])
            already = float(row["co2"].iloc[0]) >= CO2_LIMIT
            out.update({"ventilation_alert": bool(already or p_vent >= st["ventilation_threshold"]),
                        "ventilation_probability": round(p_vent, 3), "co2_over_limit_now": already})
        return out

    def out_of_range(self, raw, space, last_label):
        """최근 칸([t−5, t))에 허용 범위 밖 원시값을 보낸 센서 {항목: [센서]}. 전처리는 이 값을 지우므로 따로 알린다."""
        if raw.empty:
            return {}
        recent = raw[(raw["time"] >= last_label - pd.Timedelta(minutes=self.settings.interval_minutes))
                     & (raw["space"].astype(str) == space)]
        out = {}
        for metric, group in recent.groupby(recent["metric"].astype(str), observed=True):
            if metric not in self.settings.ranges:
                continue
            lo, hi = self.settings.ranges[metric]
            bad = group[~group["value"].between(lo, hi)]
            if len(bad):
                out[metric] = sorted(bad["device_id"].astype(str).unique().tolist())
        return out

    def self_check(self, slug, frame, forecast, last_label):
        """60분 전에 낸 예측을 지금 실제값으로 채점한다 (심화 B). 최근 24시간 평균 절대 오차."""
        out = {}
        for metric in ("co2", "temperature"):
            fc = (forecast or {}).get(metric)
            if fc:
                target = last_label + pd.Timedelta(minutes=60)
                self.pending[(slug, metric, target)] = fc["points"][-1]["value"]
            errors = self.scored.setdefault((slug, metric), [])
            for key in [k for k in self.pending if k[0] == slug and k[1] == metric and k[2] <= last_label]:
                predicted = self.pending.pop(key)
                actual = frame[metric].get(key[2]) if metric in frame else None
                if actual is not None and not np.isnan(actual):
                    errors.append((key[2], abs(predicted - float(actual))))
            errors[:] = [e for e in errors if e[0] > last_label - pd.Timedelta(hours=24)]
            if errors:
                out[metric] = {"mae_60": round(float(np.mean([e[1] for e in errors])), 2), "n": len(errors)}
        return out

    # ---------- 한 주기 ----------

    def run_cycle(self, now=None):
        began = time.perf_counter()
        now = now or now_utc()
        raw, last_label = self.read_raw(now)
        freshness = self.sensor_freshness(now)
        result = {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "slot": last_label.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "models": {"forecast": self.models.forecast_meta["run_id"], "manifest_created": self.models.manifest["created_at"]},
                  "spaces": {}}
        clean = None
        if len(raw):
            try:
                clean, _, _ = preprocess({m: raw[raw["metric"].astype(str) == m] for m in METRICS}, self.settings)
            except ValueError:
                clean = None
        frames = space_frames(clean, self.settings) if clean is not None else {}
        for spec in self.settings.spaces:
            space = spec.space
            stale = [f for f in freshness if f["space"] == space]
            entry = {"space": space, "slug": spec.slug, "slot": result["slot"],
                     "sensors": {"total": len(stale), "stale": [f["device_id"] for f in stale if f["stale"]],
                                 "last_seen": max((f["last_seen"] for f in stale), default=None)}}
            alerts = []
            if stale and all(f["stale"] for f in stale) or not stale:
                alerts.append({"type": "data_gap", "level": "warning", "message": "이 공간의 센서 데이터가 끊김"})
            elif any(f["stale"] for f in stale):
                alerts.append({"type": "sensor_gap", "level": "info",
                               "message": f"일부 센서 끊김: {', '.join(entry['sensors']['stale'])}"})
            for metric, devices in self.out_of_range(raw, space, last_label).items():
                lo, hi = self.settings.ranges[metric]
                alerts.append({"type": "rule_out_of_range", "level": "warning", "cause": metric, "devices": devices,
                               "message": f"{metric} 값이 허용 범위 [{lo:g}, {hi:g}] 밖 (센서 {', '.join(devices)}) — 계산에서 뺌"})
            frame = frames.get(space)
            if frame is not None and len(frame) and frame.index[-1] < last_label:
                frame = frame.reindex(frame.index.append(pd.date_range(frame.index[-1] + pd.Timedelta(minutes=5), last_label, freq="5min")))
            if frame is None or frame.empty:
                entry.update({"forecast": None, "anomaly": None, "state": None, "rules": []})
            else:
                entry["forecast"], why = self.forecast(clean, space, last_label, frame)
                if why:
                    entry["forecast_unavailable"] = why
                entry["anomaly"] = self.anomaly(frame, space, last_label)
                entry["rules"] = self.rule_alarms(frame, space, last_label)
                entry["state"] = self.state(frame, space, last_label)
                entry["monitor"] = self.self_check(spec.slug, frame, entry["forecast"], last_label)
                limit = float(self.cfg.get("monitor", {}).get("co2_mae_alert", 75))
                co2_check = entry["monitor"].get("co2")
                if co2_check and co2_check["n"] >= 12 and co2_check["mae_60"] > limit:
                    alerts.append({"type": "forecast_drift", "level": "info", "mae_60": co2_check["mae_60"],
                                   "message": f"최근 24시간 CO₂ 60분 예측 오차 {co2_check['mae_60']:.0f} ppm (기준 {limit:.0f}) — 재학습 검토"})
                co2 = (entry["forecast"] or {}).get("co2")
                if co2 and co2["now"] < CO2_LIMIT:
                    over = [p["minutes"] for p in co2["points"] if p["value"] >= CO2_LIMIT]
                    if over:
                        alerts.append({"type": "co2_forecast", "level": "warning", "minutes": over[0],
                                       "message": f"{over[0]}분 뒤 CO₂ 1,000 ppm 초과 예상"})
                if (entry["state"] or {}).get("ventilation_alert"):
                    alerts.append({"type": "ventilation", "level": "warning", "message": "곧 환기 필요 (30분 안 1,000 ppm)"})
                if (entry["anomaly"] or {}).get("is_anomaly"):
                    alerts.append({"type": "anomaly", "level": "warning", "cause": entry["anomaly"]["cause"],
                                   "message": f"이상 의심 (원인 항목: {entry['anomaly']['cause']})"})
                for r in entry["rules"]:
                    alerts.append({"type": f"rule_{r['rule']}", "level": "warning", "cause": r["cause"],
                                   "message": f"규칙 경보 {r['rule']} ({r['cause']})"})
            entry["alerts"] = alerts
            result["spaces"][spec.slug] = entry
        result["cycle_ms"] = round((time.perf_counter() - began) * 1000, 1)
        result["raw_rows"] = int(len(raw))
        return result


def utc_text(ts=None):
    return (ts or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
