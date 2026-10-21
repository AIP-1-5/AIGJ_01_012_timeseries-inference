"""합성 고장 주입 (spec FR-001). 과제 문서의 고장 5종 모습을 따른다. 원본은 바꾸지 않고 복사본에 심는다.

  spike        한 칸만 크게 튐 (CO₂ 또는 온도)
  stuck        1~4시간 동안 직전 값 반복 (CO₂·온도·습도 중 하나)
  drift        CO₂가 6시간 동안 수백 ppm 선형 상승, 이후 18시간 유지 (센서 노화는 저절로 돌아오지 않는다)
  cooling      실습실 수업 중 온도가 2시간 동안 3~5℃ 상승, 수업 끝(18:00)까지 유지
  ventilation  실습실 수업 블록 동안 CO₂ 상승 폭이 1.8배
"""

import numpy as np
import pandas as pd

from pipeline.schedule import holiday_dates

KINDS = ("spike", "stuck", "drift", "cooling", "ventilation")
STEP = pd.Timedelta(minutes=5)
GAP = pd.Timedelta(hours=3)          # 같은 공간의 고장 사이 최소 간격


def _free(taken, space, start, end):
    return all(not (s == space and start - GAP < e and end + GAP > b) for s, b, e in taken)


def _class_blocks(frame_index, tz, schedule, space):
    """일정 있는 날의 수업 블록 (시작, 끝) 목록 (UTC)."""
    lab = schedule.for_space(space)
    if lab is None:
        return []
    days = sorted(set(frame_index.tz_convert(tz).date))
    off = holiday_dates(schedule.holidays, {d.year for d in days})
    blocks = []
    starts = [e for e in lab.events if e.kind == "start"]
    for day in days:
        if day.weekday() not in lab.weekdays or day in off:
            continue
        for event in starts:
            following = [e for e in lab.events if e.time > event.time]
            if not following:
                continue
            begin = pd.Timestamp(f"{day} {event.time}", tz=tz).tz_convert("UTC")
            finish = pd.Timestamp(f"{day} {following[0].time}", tz=tz).tz_convert("UTC")
            blocks.append((begin, finish))
    return blocks


def inject(frames, start, end, cfg, seed, settings, schedule):
    """기간 [start, end)의 복사본에 고장을 심는다. (복사본 {공간: 표}, 고장 목록 DataFrame)"""
    rng = np.random.default_rng(seed)
    out = {space: frame[(frame.index >= start) & (frame.index < end)].copy() for space, frame in frames.items()}
    tz = settings.timezone
    taken, events = [], []
    co2_spaces = [s for s, f in out.items() if f["co2"].notna().mean() > 0.5]
    temp_spaces = [s for s, f in out.items() if f["temperature"].notna().mean() > 0.5]
    lab_spaces = [s for s in co2_spaces if schedule.for_space(s) is not None]
    n = int(cfg.get("per_type", 8))

    def coverage_ok(frame, metric, b, e, need=0.9):
        part = frame.loc[(frame.index >= b) & (frame.index <= e), metric]
        return len(part) > 0 and part.notna().mean() >= need

    def place(kind, attempts=4000):
        for _ in range(attempts):
            if kind == "spike":
                metric = "co2" if rng.random() < 0.6 else "temperature"
                space = rng.choice(co2_spaces if metric == "co2" else temp_spaces)
                frame = out[space]
                obs = frame.index[frame[f"obs_{metric}"] & frame[metric].notna()]
                if len(obs) < 10:
                    continue
                t = obs[rng.integers(5, len(obs) - 5)]
                b, e = t, t
                if not _free(taken, space, b, e):
                    continue
                lo, hi = cfg.get(f"spike_{metric}", [400, 1200] if metric == "co2" else [4, 8])
                size = rng.uniform(lo, hi) * (1 if metric == "co2" or rng.random() < 0.5 else -1)
                frame.loc[t, metric] = frame.loc[t, metric] + size
                return dict(space=space, metric=metric, kind=kind, start=b, end=e, size=float(size))
            if kind == "stuck":
                metric = rng.choice(["co2", "temperature", "humidity"])
                pool = [s for s, f in out.items() if f[metric].notna().mean() > 0.5]
                space = rng.choice(pool)
                frame = out[space]
                hours = rng.uniform(*cfg.get("stuck_hours", [1, 4]))
                b = frame.index[rng.integers(12, len(frame) - 60)]
                e = b + pd.Timedelta(hours=hours)
                if not _free(taken, space, b, e) or not coverage_ok(frame, metric, b - STEP, e):
                    continue
                last = frame.loc[:b - STEP, metric].dropna()
                if last.empty:
                    continue
                window = (frame.index >= b) & (frame.index <= e) & frame[metric].notna()
                frame.loc[window, metric] = last.iloc[-1]
                return dict(space=space, metric=metric, kind=kind, start=b, end=e, size=float(hours))
            if kind == "drift":
                space = rng.choice(co2_spaces)
                frame = out[space]
                b = frame.index[rng.integers(12, max(13, len(frame) - 24 * 12 - 1))]
                ramp, e = b + pd.Timedelta(hours=6), b + pd.Timedelta(hours=24)
                if e >= frame.index[-1] or not _free(taken, space, b, e) or not coverage_ok(frame, "co2", b, e, 0.8):
                    continue
                size = rng.uniform(*cfg.get("drift_ppm", [200, 500]))
                window = (frame.index >= b) & (frame.index <= e)
                elapsed = (frame.index[window] - b) / (ramp - b)
                frame.loc[window, "co2"] = frame.loc[window, "co2"] + size * np.clip(elapsed, 0, 1)
                return dict(space=space, metric="co2", kind=kind, start=b, end=e, size=float(size))
            if kind in ("cooling", "ventilation"):
                if not lab_spaces:
                    return None
                space = rng.choice(lab_spaces)
                frame = out[space]
                blocks = [blk for blk in _class_blocks(frame.index, tz, schedule, space)
                          if blk[0] >= frame.index[0] + pd.Timedelta(hours=1) and blk[1] <= frame.index[-1]]
                if not blocks:
                    return None
                block_start, block_end = blocks[rng.integers(len(blocks))]
                if kind == "cooling":
                    metric = "temperature"
                    latest = block_end - pd.Timedelta(hours=2)
                    if latest <= block_start:
                        continue
                    b = block_start + (latest - block_start) * rng.random()
                    b = b.floor("5min")
                    e = block_end
                    if not _free(taken, space, b, e) or not coverage_ok(frame, metric, b, e):
                        continue
                    size = rng.uniform(*cfg.get("cooling_rise", [3, 5]))
                    window = (frame.index >= b) & (frame.index <= e)
                    elapsed = (frame.index[window] - b) / pd.Timedelta(hours=2)
                    frame.loc[window, metric] = frame.loc[window, metric] + size * np.clip(elapsed, 0, 1)
                    return dict(space=space, metric=metric, kind=kind, start=b, end=e, size=float(size))
                metric = "co2"
                b, e = block_start, block_end
                before = frame.loc[(frame.index > b - pd.Timedelta(minutes=30)) & (frame.index <= b), metric].dropna()
                window = (frame.index > b) & (frame.index <= e)
                if before.empty or not _free(taken, space, b, e) or not coverage_ok(frame, metric, b, e):
                    continue
                base = before.mean()
                rise = frame.loc[window, metric] - base
                if rise.max() < 200:                   # 사람이 거의 없던 블록은 환기 정지가 드러나지 않는다
                    continue
                factor = float(cfg.get("ventilation_factor", 1.8))
                frame.loc[window, metric] = base + np.where(rise > 0, rise * factor, rise)
                return dict(space=space, metric=metric, kind=kind, start=b, end=e, size=factor)
        return None

    for kind in KINDS:
        for _ in range(n):
            event = place(kind)
            if event is None:
                continue
            taken.append((event["space"], event["start"], event["end"]))
            events.append(event)
    table = pd.DataFrame(events, columns=["space", "metric", "kind", "start", "end", "size"])
    table = table.sort_values(["start", "space"]).reset_index(drop=True)
    table.insert(0, "event_id", range(1, len(table) + 1))
    return out, table
