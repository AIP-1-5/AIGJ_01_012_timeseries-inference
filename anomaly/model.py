"""정상만 보고 배우는 오토인코더 (spec US2). 과제 8 서비스가 같은 특성 함수를 쓴다.

창 = 최근 1시간(12칸)의 co2·temperature·humidity 정규화 값(z)과 5분 변화량(Δz).
조건 = 하루 시각 sin·cos, 일정 있는 날, 공간 원-핫 (재구성하지 않고 입력으로만).
점수 = 마지막 3칸(15분)의 z·Δz 재구성 절대 오차 평균. 항목별 오차가 가장 큰 것이 원인 항목.
"""

import copy

import numpy as np
import pandas as pd
import torch
from torch import nn

from .data import METRICS
from .features import TAIL, window_features  # noqa: F401  (예전 경로 호환)

def gather(values, mask, cond, positions, window):
    idx = positions[:, None] + np.arange(-window + 1, 1)[None, :]
    return values[idx], mask[idx].astype(np.float32), cond[positions]


class AutoEncoder(nn.Module):
    def __init__(self, window, n_cond, hidden=64, bottleneck=16):
        super().__init__()
        size = window * 6
        self.window = window
        self.encoder = nn.Sequential(nn.Linear(size + n_cond, hidden), nn.ReLU(), nn.Linear(hidden, bottleneck), nn.ReLU())
        self.decoder = nn.Sequential(nn.Linear(bottleneck + n_cond, hidden), nn.ReLU(), nn.Linear(hidden, size))

    def forward(self, x, cond):
        flat = x.flatten(1)
        code = self.encoder(torch.cat([flat, cond], dim=1))
        return self.decoder(torch.cat([code, cond], dim=1)).view(-1, self.window, 6)


def masked_mse(recon, x, mask):
    return (((recon - x) ** 2) * mask).sum() / mask.sum().clamp(min=1)


class Dataset:
    """공간별 특성을 이어 붙인 것. positions는 (공간 번호, 위치) 대신 이어 붙인 배열의 위치."""

    def __init__(self, frames, spaces, normalization, schedule, window, tz):
        parts, offset = [], 0
        self.space_of, self.times, self.valid = [], [], []
        for sid, space in enumerate(spaces):
            if space not in frames or frames[space].empty:
                continue
            values, mask, cond, valid = window_features(frames[space], space, spaces, normalization, schedule, window, tz)
            parts.append((values, mask, cond))
            self.space_of.append(np.full(len(values), sid))
            self.times.append(np.asarray(frames[space].index.tz_convert("UTC").tz_localize(None)))
            self.valid.append(valid + offset)
            offset += len(values)
        self.values = np.concatenate([p[0] for p in parts])
        self.mask = np.concatenate([p[1] for p in parts])
        self.cond = np.concatenate([p[2] for p in parts])
        self.space_of = np.concatenate(self.space_of)
        self.times = np.concatenate(self.times)
        self.valid = np.concatenate(self.valid)
        self.window = window
        self.spaces = spaces

    def batch(self, positions):
        x, m, c = gather(self.values, self.mask, self.cond, positions, self.window)
        return torch.from_numpy(x), torch.from_numpy(m), torch.from_numpy(c)


def train_autoencoder(train_ds, val_ds, cfg, log=print):
    torch.manual_seed(int(cfg.get("seed", 1)))
    rng = np.random.default_rng(int(cfg.get("seed", 1)))
    model = AutoEncoder(train_ds.window, train_ds.cond.shape[1], int(cfg.get("hidden", 64)), int(cfg.get("bottleneck", 16)))
    optimizer = torch.optim.Adam(model.parameters(), lr=float(cfg.get("learning_rate", 1e-3)))
    batch_size = int(cfg.get("batch_size", 1024))
    best, best_loss, bad = None, float("inf"), 0
    for epoch in range(1, int(cfg.get("epochs", 20)) + 1):
        model.train()
        order = rng.permutation(train_ds.valid)
        total = 0.0
        for start in range(0, len(order), batch_size):
            x, m, c = train_ds.batch(order[start:start + batch_size])
            loss = masked_mse(model(x, c), x, m)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            total += float(loss.detach()) * len(x)
        val_loss = evaluate_loss(model, val_ds, batch_size)
        log(f"[AE] 에폭 {epoch:>2}: 학습 {total / len(order):.4f}, 검증(정상) {val_loss:.4f}")
        if val_loss < best_loss - 1e-6:
            best, best_loss, bad = copy.deepcopy(model.state_dict()), val_loss, 0
        else:
            bad += 1
            if bad >= int(cfg.get("patience", 4)):
                break
    model.load_state_dict(best)
    model.eval()
    return model


@torch.no_grad()
def evaluate_loss(model, ds, batch_size=4096):
    model.eval()
    total, count = 0.0, 0
    for start in range(0, len(ds.valid), batch_size):
        x, m, c = ds.batch(ds.valid[start:start + batch_size])
        total += float(masked_mse(model(x, c), x, m)) * len(x)
        count += len(x)
    return total / max(count, 1)


@torch.no_grad()
def scores(model, ds, batch_size=4096):
    """시각별 점수와 원인 항목. DataFrame(space, time, score, cause)."""
    model.eval()
    out_score, out_cause = [], []
    for start in range(0, len(ds.valid), batch_size):
        x, m, c = ds.batch(ds.valid[start:start + batch_size])
        err = ((model(x, c) - x).abs() * m)[:, -TAIL:, :]               # (B, 3, 6)
        per_metric = (err[:, :, :3] + err[:, :, 3:]).sum(dim=1)          # 항목별 (z + Δz)
        count = (m[:, -TAIL:, :3] + m[:, -TAIL:, 3:]).sum(dim=1).clamp(min=1)
        per_metric = per_metric / count
        present = m[:, -TAIL:, :3].sum(dim=1) > 0
        score = (per_metric * present).sum(dim=1) / present.sum(dim=1).clamp(min=1)
        out_score.append(score.numpy())
        out_cause.append(np.asarray(METRICS)[per_metric.masked_fill(~present, -1).argmax(dim=1).numpy()])
    pos = ds.valid
    return pd.DataFrame({
        "space": np.asarray(ds.spaces)[ds.space_of[pos]],
        "time": pd.to_datetime(ds.times[pos]).tz_localize("UTC"),
        "score": np.concatenate(out_score) if out_score else [],
        "cause": np.concatenate(out_cause) if out_cause else [],
    })
