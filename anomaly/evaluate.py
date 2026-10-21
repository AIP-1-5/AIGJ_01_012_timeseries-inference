"""경보 묶기와 평가 (spec FR-004·FR-005).

경보 칸 → 같은 공간에서 merge_minutes 안에 이어진 경보는 한 이벤트.
고장 구간 [시작, 끝 + grace] 안에 경보 이벤트가 하나라도 걸리면 탐지, 지연 = 그 구간 안 첫 경보 − 시작.
어떤 고장 구간과도 겹치지 않는 경보 이벤트 = 헛경보. 하루당 = 헛경보 수 / 기간 일수 (전체 공간 합).
"""

import numpy as np
import pandas as pd


def alarm_events(alarms, merge_minutes=60):
    """경보 칸 DataFrame(space, time, detector, cause) → 이벤트(space, start, end, detector, cause, n)."""
    if alarms.empty:
        return pd.DataFrame(columns=["space", "start", "end", "detectors", "cause", "n"])
    rows = []
    gap = pd.Timedelta(minutes=merge_minutes)
    for space, group in alarms.sort_values("time").groupby("space", sort=True):
        times = group["time"].to_numpy()
        new = np.r_[True, np.diff(times) > gap.to_timedelta64()]
        ids = np.cumsum(new)
        for _, part in group.groupby(ids):
            rows.append({
                "space": space, "start": part["time"].min(), "end": part["time"].max(),
                "detectors": ",".join(sorted(set(part["detector"]))),
                "cause": part["cause"].mode().iloc[0], "n": len(part),
            })
    return pd.DataFrame(rows)


def match(faults, events, grace_minutes=60):
    """고장마다 탐지 여부·지연·원인 일치, 그리고 헛경보 이벤트."""
    grace = pd.Timedelta(minutes=grace_minutes)
    results = []
    used = np.zeros(len(events), bool)
    for fault in faults.itertuples():
        window_end = fault.end + grace
        hit = (events["space"] == fault.space) & (events["end"] >= fault.start) & (events["start"] <= window_end)
        if hit.any():
            matched = events[hit]
            used |= hit.to_numpy()
            first = max(matched["start"].min(), fault.start)
            results.append({"event_id": fault.event_id, "kind": fault.kind, "space": fault.space, "metric": fault.metric,
                            "detected": True, "delay_min": (first - fault.start).total_seconds() / 60,
                            "cause_ok": bool((matched["cause"] == fault.metric).any()),
                            "detectors": ",".join(sorted(set(",".join(matched["detectors"]).split(","))))})
        else:
            results.append({"event_id": fault.event_id, "kind": fault.kind, "space": fault.space, "metric": fault.metric,
                            "detected": False, "delay_min": np.nan, "cause_ok": False, "detectors": ""})
    return pd.DataFrame(results), events[~used]


def score_alarms(scores, threshold):
    hit = scores[scores["score"] > threshold]
    return pd.DataFrame({"space": hit["space"], "time": hit["time"], "detector": "model", "cause": hit["cause"]})


def rule_alarms(rule_rows):
    return pd.DataFrame({"space": rule_rows["space"], "time": rule_rows["time"],
                         "detector": "rule:" + rule_rows["rule"], "cause": rule_rows["cause"]})


def summarize(faults, alarms, days, merge_minutes, grace_minutes):
    """탐지 결과 표(고장별), 종류별 요약, 전체 요약."""
    events = alarm_events(alarms, merge_minutes)
    per_fault, false_events = match(faults, events, grace_minutes)
    by_kind = per_fault.groupby("kind").agg(total=("event_id", "size"), detected=("detected", "sum"),
                                            delay_median=("delay_min", "median"), cause_ok=("cause_ok", "sum")).reset_index()
    overall = {
        "total": int(len(per_fault)), "detected": int(per_fault["detected"].sum()),
        "rate": float(per_fault["detected"].mean()) if len(per_fault) else 0.0,
        "delay_median_min": float(per_fault["delay_min"].median()) if per_fault["detected"].any() else None,
        "false_alarms": int(len(false_events)), "false_per_day": float(len(false_events) / days) if days else None,
        "cause_ok": int(per_fault["cause_ok"].sum()),
    }
    return per_fault, by_kind, overall, false_events


def pick_threshold(candidates, evaluate, target_false_per_day):
    """헛경보 ≤ 목표인 후보 중 탐지율이 가장 높은 임계값 (같으면 높은 임계값 = 더 보수적)."""
    rows = []
    for threshold in candidates:
        overall = evaluate(threshold)
        rows.append({"threshold": float(threshold), **overall})
    table = pd.DataFrame(rows)
    ok = table[table["false_per_day"] <= target_false_per_day]
    chosen = (ok.sort_values(["rate", "threshold"], ascending=[False, False]).iloc[0] if len(ok)
              else table.sort_values(["false_per_day", "threshold"], ascending=[True, False]).iloc[0])
    return float(chosen["threshold"]), table
