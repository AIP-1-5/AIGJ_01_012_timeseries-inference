"""학습 (research.md R5·R6·R7). 학습 기간 표본으로 학습하고, 검증 기간으로만 조기 종료한다.

실행 하나 = (모델 종류, 변형, 과거 길이, 씨앗, 정답 종류). 결과는 data/forecast/runs/<run_id>/.
"""

import copy
import json
import platform
import random
import time
from datetime import datetime, timezone

import numpy as np
import torch

from pipeline.baselines import git_commit

from .model import build_model, count_parameters, masked_l1
from .settings import HORIZON_STEPS, TARGET_METRICS

HORIZONS = [5 * (k + 1) for k in range(HORIZON_STEPS)]


def run_id(kind, variant, lookback_minutes, seed, target="delta"):
    return f"{kind}-{variant}-L{lookback_minutes}-s{seed}" + ("-value" if target == "value" else "")


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def pick_device(name):
    if name == "mps" and torch.backends.mps.is_available():
        return torch.device("mps")
    if name.startswith("cuda") and torch.cuda.is_available():
        return torch.device(name)
    return torch.device("cpu")


def to_tensors(batch, device):
    return {k: torch.from_numpy(np.ascontiguousarray(v)).to(device) for k, v in batch.items()}


def iterate(samples, positions, batch_size, rng=None):
    order = rng.permutation(positions) if rng is not None else positions
    for start in range(0, len(order), batch_size):
        yield samples.batch(order[start:start + batch_size])


@torch.no_grad()
def predict_delta(model, samples, positions, batch_size=2048, device=torch.device("cpu")):
    """표본 위치들 → (N, 12, 2) 변화량 예측 (정규화 단위). target="value" 모델이면 값 − y(t)로 바꾼다."""
    model.eval()
    out = []
    for batch in iterate(samples, positions, batch_size):
        t = to_tensors(batch, device)
        pred = model(t["past"], t["future"], t["space"]).cpu().numpy()
        if samples.target_kind == "value":
            pred = pred - batch["base"][:, None, :]
        out.append(pred)
    return np.concatenate(out) if out else np.zeros((0, HORIZON_STEPS, 2), np.float32)


def epoch_loss(model, samples, positions, batch_size, device, optimizer=None, rng=None):
    training = optimizer is not None
    model.train(training)
    total, count = 0.0, 0
    for batch in iterate(samples, positions, batch_size, rng):
        t = to_tensors(batch, device)
        with torch.set_grad_enabled(training):
            loss = masked_l1(model(t["past"], t["future"], t["space"]), t["target"], t["mask"])
            if training:
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
        total += float(loss) * len(batch["base"])
        count += len(batch["base"])
    return total / max(count, 1)


def delta_metrics(samples, positions, delta):
    """검증용 지표 (원래 단위): h × 항목별 모델 MAE와 같은 칸의 현재값 유지 MAE, 공간별·전체."""
    positions = np.asarray(positions)
    future_idx = positions[:, None] + np.arange(1, HORIZON_STEPS + 1)[None, :]
    truth = samples.z[future_idx] - samples.z[positions][:, None, :]
    mask = samples.observed[future_idx] & samples.observed[positions][:, None, :]
    std = samples.std[samples.space_id[positions]][:, None, :]
    model_err = np.abs(np.nan_to_num(delta - truth)) * std
    persist_err = np.abs(np.nan_to_num(truth)) * std
    result = {"overall": {}, "by_space": {}}

    def summarize(rows):
        table = {}
        for j, metric in enumerate(TARGET_METRICS):
            table[metric] = {}
            for k, horizon in enumerate(HORIZONS):
                m = mask[rows, k, j]
                n = int(m.sum())
                table[metric][str(horizon)] = {
                    "mae": float(model_err[rows, k, j][m].mean()) if n else None,
                    "persistence": float(persist_err[rows, k, j][m].mean()) if n else None,
                    "n": n,
                }
        return table

    result["overall"] = summarize(np.arange(len(positions)))
    for sid, space in enumerate(samples.spaces):
        rows = np.flatnonzero(samples.space_id[positions] == sid)
        if len(rows):
            result["by_space"][space] = summarize(rows)
    ratios = [result["overall"][m]["60"]["mae"] / result["overall"][m]["60"]["persistence"]
              for m in TARGET_METRICS if result["overall"][m]["60"]["n"]]
    result["score"] = float(np.mean(ratios)) if ratios else None
    result["score_rule"] = "검증 기간 60분 (co2 MAE/현재값 유지 + 온도 MAE/현재값 유지) / 2. 작을수록 좋음"
    return result


def train_run(fsettings, samples, kind, variant, seed, target="delta", log=print):
    """한 번 학습. 실행 폴더 경로를 돌려준다."""
    set_seed(seed)
    device = pick_device(fsettings.device)
    rid = run_id(kind, variant, fsettings.lookback_minutes, seed, target)
    out = fsettings.runs_dir / rid
    out.mkdir(parents=True, exist_ok=True)
    n_past = samples.feats.shape[1]
    n_future = samples.future_feats.shape[1]
    model = build_model(kind, samples.lookback, n_past, n_future, len(samples.spaces),
                        fsettings.hidden, fsettings.layers, fsettings.dropout).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=fsettings.learning_rate)
    rng = np.random.default_rng(seed)
    train_pos, val_pos = samples.positions["train"], samples.positions["val"]
    log(f"[{rid}] 표본 학습 {len(train_pos):,} / 검증 {len(val_pos):,}, 파라미터 {count_parameters(model):,}, 장치 {device}")
    best, best_loss, bad, history = None, float("inf"), 0, []
    began = time.monotonic()
    for epoch in range(1, fsettings.max_epochs + 1):
        t0 = time.monotonic()
        train_loss = epoch_loss(model, samples, train_pos, fsettings.batch_size, device, optimizer, rng)
        val_loss = epoch_loss(model, samples, val_pos, 4096, device)
        history.append({"epoch": epoch, "train_loss": train_loss, "val_loss": val_loss,
                        "seconds": round(time.monotonic() - t0, 1)})
        improved = val_loss < best_loss - 1e-6
        if improved:
            best, best_loss, bad = copy.deepcopy(model.state_dict()), val_loss, 0
        else:
            bad += 1
        log(f"[{rid}] 에폭 {epoch:>2}: 학습 {train_loss:.4f}, 검증 {val_loss:.4f}{' *' if improved else ''} "
            f"({history[-1]['seconds']}초)")
        if bad >= fsettings.patience:
            break
    model.load_state_dict(best)
    best_epoch = min(history, key=lambda h: h["val_loss"])["epoch"]
    metrics = delta_metrics(samples, val_pos, predict_delta(model, samples, val_pos, device=device))
    meta = {
        "run_id": rid, "kind": kind, "variant": variant, "seed": seed, "target": target,
        "lookback_minutes": fsettings.lookback_minutes, "lookback_steps": samples.lookback,
        "n_past": n_past, "n_future": n_future, "n_spaces": len(samples.spaces), "spaces": samples.spaces,
        "hidden": fsettings.hidden, "layers": fsettings.layers, "dropout": fsettings.dropout,
        "feature_names": samples.feature_names,
        "parameters": count_parameters(model),
        "best_epoch": best_epoch, "epochs_run": len(history),
        "batch_size": fsettings.batch_size, "learning_rate": fsettings.learning_rate,
        "device": str(device), "machine": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(), "torch": torch.__version__,
        "code_commit": git_commit(fsettings.pipeline.root),
        "train_samples": int(len(train_pos)), "val_samples": int(len(val_pos)),
        "minutes": round((time.monotonic() - began) / 60, 1),
        "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    torch.save({"state_dict": model.state_dict(), "meta": meta}, out / "model.pt")
    (out / "config.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "val_metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out / "history.csv").open("w", encoding="utf-8") as fp:
        fp.write("epoch,train_loss,val_loss,seconds\n")
        for h in history:
            fp.write(f"{h['epoch']},{h['train_loss']:.6f},{h['val_loss']:.6f},{h['seconds']}\n")
    co2, temp = metrics["overall"]["co2"]["60"], metrics["overall"]["temperature"]["60"]
    log(f"[{rid}] 끝 (에폭 {best_epoch} 선택, {meta['minutes']}분). 검증 60분: "
        f"CO₂ {co2['mae']:.2f} vs 현재값 유지 {co2['persistence']:.2f} ppm, "
        f"온도 {temp['mae']:.3f} vs {temp['persistence']:.3f} ℃, 점수 {metrics['score']:.3f}")
    return out


def load_model(run_dir, device=torch.device("cpu")):
    checkpoint = torch.load(run_dir / "model.pt", map_location=device, weights_only=False)
    meta = checkpoint["meta"]
    model = build_model(meta["kind"], meta["lookback_steps"], meta["n_past"], meta["n_future"], meta["n_spaces"],
                        meta["hidden"], meta["layers"], meta["dropout"]).to(device)
    model.load_state_dict(checkpoint["state_dict"])
    model.eval()
    return model, meta
