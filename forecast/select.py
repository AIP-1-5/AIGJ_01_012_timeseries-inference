"""최종 모델 고르기 (research.md R7). 검증 기간 결과만 본다. 시험 기간 결과는 이 단계에서 보지 않는다."""

import json
from datetime import datetime, timezone

from .evaluate import sha256


def candidates(fsettings):
    rows = []
    for run_dir in sorted(fsettings.runs_dir.glob("*/")):
        metrics_path = run_dir / "val_metrics.json"
        if not metrics_path.exists() or not (run_dir / "model.pt").exists():
            continue
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        meta = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
        rows.append({
            "run_id": run_dir.name, "kind": meta["kind"], "variant": meta["variant"], "seed": meta["seed"],
            "target": meta["target"], "val_score": metrics["score"],
            "val_co2_60": metrics["overall"]["co2"]["60"]["mae"],
            "val_temperature_60": metrics["overall"]["temperature"]["60"]["mae"],
            "val_co2_60_persistence": metrics["overall"]["co2"]["60"]["persistence"],
            "val_temperature_60_persistence": metrics["overall"]["temperature"]["60"]["persistence"],
        })
    return rows


def select(fsettings, force=False, log=print):
    path = fsettings.selection_path
    test_log = fsettings.reports_dir / "003-test-log.md"
    if path.exists() and not force:
        chosen = json.loads(path.read_text(encoding="utf-8"))
        log(f"이미 골랐다: {chosen['chosen']} ({chosen['decided_at']}). 다시 고르려면 --force")
        return 0
    if force and test_log.exists():
        log("주의: 시험 평가 기록이 이미 있다. 시험 결과를 본 뒤 다시 고르면 시험 기간이 선택에 쓰인 것이 된다. "
            "다시 고른 사실과 이유를 보고서에 적어야 한다")
    rows = [r for r in candidates(fsettings) if r["val_score"] is not None]
    if not rows:
        log("고를 실행이 없다. 먼저 train을 실행한다")
        return 1
    best = min(rows, key=lambda r: (r["val_score"], r["run_id"]))
    record = {
        "chosen": best["run_id"],
        "criterion": "검증 기간 60분 (CO₂ MAE/현재값 유지 + 온도 MAE/현재값 유지) / 2가 가장 작은 실행",
        "candidates": sorted(rows, key=lambda r: r["val_score"]),
        "model_sha256": sha256(fsettings.runs_dir / best["run_id"] / "model.pt"),
        "decided_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "replaced_after_test": bool(force and test_log.exists()),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"최종 모델: {best['run_id']} (검증 점수 {best['val_score']:.3f}, 후보 {len(rows)}개) → {path}")
    for r in record["candidates"][:10]:
        log(f"  {r['run_id']:<32} 점수 {r['val_score']:.3f}  CO₂60 {r['val_co2_60']:.2f}  온도60 {r['val_temperature_60']:.3f}")
    return 0
