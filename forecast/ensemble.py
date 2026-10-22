"""2차 선택: 이미 학습한 실행들의 평균(앙상블)과 시점별 섞기를 검증 기간으로만 고른다.

1차 선택(`select`, 실행 하나)은 시험 기간(2026-09-23 ~ 10-21)에서 이미 평가했다. 그 결과를 보고 같은 기간에서
다시 고르면 시험 기간이 선택에 쓰인 것이 되므로, 2차 선택의 시험은 아직 아무도 보지 않은 새 기간
(2026-10-22 ~ 11-05, 끝 미포함)에서 한 번만 한다. 이 파일은 검증 기간 예측만 계산한다.

후보:
  single      실행 하나 (1차와 같음)
  seed_avg    같은 변형의 씨앗 평균 (변형마다)
  all_avg     GRU 변화량 실행 전체 평균
  blend       위 평균 중 가장 좋은 것과 값 직접 예측 실행을 시점·항목별 가중치 w로 섞음 (w는 검증 MAE 최소)
"""

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .settings import HORIZON_STEPS, TARGET_METRICS
from .train import delta_metrics, load_model, pick_device, predict_delta

SECOND_TEST = {"start": "2026-10-22T00:00:00+09:00", "end": "2026-11-05T00:00:00+09:00"}
WEIGHTS = np.round(np.arange(0.0, 1.01, 0.1), 2)


def run_meta(fsettings, name):
    return json.loads((fsettings.runs_dir / name / "config.json").read_text(encoding="utf-8"))


def keyed_delta(fsettings, samples, name, device):
    """검증 표본 예측 → (키 DataFrame[space_id, time], (N, 12, 2) 변화량)."""
    model, _ = load_model(fsettings.runs_dir / name, device)
    positions = samples.positions["val"]
    delta = predict_delta(model, samples, positions, device=device)
    keys = pd.DataFrame({"space": samples.space_id[positions], "time": samples.times[positions]})
    return keys, delta


def align(reference, keys, delta):
    """다른 표본 집합의 예측을 기준 표본 위치 순서로 맞춘다. 없는 칸은 NaN."""
    ref_keys = pd.DataFrame({"space": reference.space_id[reference.positions["val"]],
                             "time": reference.times[reference.positions["val"]]})
    index = pd.MultiIndex.from_frame(keys)
    where = index.get_indexer(pd.MultiIndex.from_frame(ref_keys))
    out = np.full((len(ref_keys), HORIZON_STEPS, 2), np.nan, dtype=np.float32)
    ok = where >= 0
    out[ok] = delta[where[ok]]
    return out


def score_of(reference, delta):
    """NaN 칸(어느 구성원이 예측 못 한 칸)은 현재값 유지와 같은 0 변화로 두지 않고 평가에서 뺀다."""
    positions = reference.positions["val"]
    keep = ~np.isnan(delta).any(axis=(1, 2))
    return delta_metrics(reference, positions[keep], delta[keep]), keep


def blend_weights(reference, avg, value, keep):
    """시점·항목별로 w·값 직접 + (1−w)·평균의 검증 MAE가 가장 작은 w."""
    positions = reference.positions["val"][keep]
    future_idx = positions[:, None] + np.arange(1, HORIZON_STEPS + 1)[None, :]
    truth = reference.z[future_idx] - reference.z[positions][:, None, :]
    mask = reference.observed[future_idx] & reference.observed[positions][:, None, :]
    weights = np.zeros((HORIZON_STEPS, 2))
    for k in range(HORIZON_STEPS):
        for j in range(2):
            m = mask[:, k, j]
            errors = [np.abs(w * value[keep][m, k, j] + (1 - w) * avg[keep][m, k, j] - truth[m, k, j]).mean() for w in WEIGHTS]
            weights[k, j] = WEIGHTS[int(np.argmin(errors))]
    return weights


def run(fsettings, log=print):
    from .dataset import samples_for

    path = fsettings.out_dir / "ensemble_selection.json"
    if path.exists():
        chosen = json.loads(path.read_text(encoding="utf-8"))
        log(f"이미 골랐다: {chosen['chosen']} ({chosen['decided_at']}). 2차 시험 전에는 다시 고르지 않는다")
        return 0
    device = pick_device(fsettings.device)
    names = sorted(p.name for p in fsettings.runs_dir.glob("*/") if (p / "model.pt").exists())
    metas = {n: run_meta(fsettings, n) for n in names}
    gru_delta = [n for n in names if metas[n]["kind"] == "gru" and metas[n]["target"] == "delta"]
    value_runs = [n for n in names if metas[n]["target"] == "value"]
    if not gru_delta:
        log("GRU 변화량 실행이 없다. 먼저 train --all")
        return 1

    cache, preds, reference = {}, {}, None
    for name in gru_delta + value_runs:
        meta = metas[name]
        key = (meta["variant"], meta["target"], meta["lookback_steps"])
        if key not in cache:
            cache.clear()
            cache[key] = samples_for(fsettings, meta["variant"], meta["target"], with_test=False, lookback=meta["lookback_steps"])
        samples = cache[key]
        if reference is None:
            reference = samples
        keys, delta = keyed_delta(fsettings, samples, name, device)
        preds[name] = align(reference, keys, delta)
        log(f"[{name}] 검증 예측 {len(keys):,}칸")

    members = {f"single:{n}": [n] for n in gru_delta + value_runs}
    for variant in sorted({metas[n]["variant"] for n in gru_delta}):
        group = [n for n in gru_delta if metas[n]["variant"] == variant]
        if len(group) > 1:
            members[f"seed_avg:{variant}"] = group
    members["all_avg:gru"] = gru_delta
    candidates = {label: np.mean([preds[n] for n in group], axis=0) for label, group in members.items()}

    # 모든 후보를 같은 칸에서 비교한다 (어느 실행이라도 예측 못 한 칸은 모두에서 뺀다)
    common = ~np.any([np.isnan(preds[n]).any(axis=(1, 2)) for n in preds], axis=0)

    def summary(delta):
        metrics, keep = score_of(reference, np.where(common[:, None, None], delta, np.nan))
        overall = metrics["overall"]
        return {"score": metrics["score"], "co2_60": overall["co2"]["60"]["mae"],
                "co2_60_persistence": overall["co2"]["60"]["persistence"],
                "temperature_60": overall["temperature"]["60"]["mae"], "n": int(keep.sum())}

    rows = {label: summary(delta) for label, delta in candidates.items()}
    averages = [label for label in candidates if not label.startswith("single:")]
    best_avg = min(averages, key=lambda label: rows[label]["score"])
    weights = {}
    for value in value_runs:
        avg = candidates[best_avg]
        w = blend_weights(reference, avg, preds[value], common)
        label = f"blend:{best_avg}+{value}"
        members[label] = members[best_avg] + [value]
        rows[label] = summary(w[None, :, :] * preds[value] + (1 - w[None, :, :]) * avg)
        weights[label] = {m: w[:, j].tolist() for j, m in enumerate(TARGET_METRICS)}

    chosen = min(rows, key=lambda label: (rows[label]["score"], label))
    record = {
        "chosen": chosen,
        "members": members[chosen],
        "value_share_by_horizon": weights.get(chosen),
        "criterion": "검증 기간 60분 (CO₂ MAE/현재값 유지 + 온도 MAE/현재값 유지) / 2가 가장 작은 후보. 시험 기간은 보지 않음",
        "candidates": dict(sorted(rows.items(), key=lambda kv: kv[1]["score"])),
        "second_test_period": SECOND_TEST,
        "first_test_period_note": "2026-09-23 ~ 10-21은 1차 선택 모델로 이미 평가했으므로 이 선택에 쓰지 않았다",
        "decided_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"2차 선택: {chosen} → {path}")
    for label, r in list(record["candidates"].items())[:12]:
        log(f"  {label:<55} 점수 {r['score']:.3f}  CO₂60 {r['co2_60']:.2f} (현재값 유지 {r['co2_60_persistence']:.2f})")
    return 0
