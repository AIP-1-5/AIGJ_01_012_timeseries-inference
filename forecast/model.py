"""모델 (research.md R4, contracts/model-io.md).

입력 past (B, L, F), future (B, 12, C), space (B, 4) → 출력 (B, 12, 2) = 12칸 × (co2, temperature).
target="delta"면 출력은 지금 값에서의 변화량이라, 출력 0이 곧 '현재값 유지'다.
"""

import torch
from torch import nn

from .settings import HORIZON_STEPS

N_TARGETS = 2


class LinearForecaster(nn.Module):
    """단순한 것부터 (constitution VII): 창을 펼쳐 한 층."""

    def __init__(self, lookback, n_past, n_future, n_spaces):
        super().__init__()
        size = lookback * n_past + HORIZON_STEPS * n_future + n_spaces
        self.out = nn.Linear(size, HORIZON_STEPS * N_TARGETS)
        nn.init.zeros_(self.out.weight)
        nn.init.zeros_(self.out.bias)              # 처음에는 현재값 유지와 같다

    def forward(self, past, future, space):
        x = torch.cat([past.flatten(1), future.flatten(1), space], dim=1)
        return self.out(x).view(-1, HORIZON_STEPS, N_TARGETS)


class GRUForecaster(nn.Module):
    """GRU가 과거 창을 읽고, 마지막 상태 ⊕ 미래 달력·일정 ⊕ 공간 → MLP → 12 × 2."""

    def __init__(self, lookback, n_past, n_future, n_spaces, hidden=64, layers=2, dropout=0.1):
        super().__init__()
        self.gru = nn.GRU(n_past, hidden, num_layers=layers, batch_first=True,
                          dropout=dropout if layers > 1 else 0.0)
        size = hidden + HORIZON_STEPS * n_future + n_spaces
        self.head = nn.Sequential(nn.Linear(size, 128), nn.ReLU(), nn.Dropout(dropout),
                                  nn.Linear(128, HORIZON_STEPS * N_TARGETS))
        nn.init.zeros_(self.head[-1].weight)
        nn.init.zeros_(self.head[-1].bias)         # 처음에는 현재값 유지와 같다

    def forward(self, past, future, space):
        _, h = self.gru(past)
        x = torch.cat([h[-1], future.flatten(1), space], dim=1)
        return self.head(x).view(-1, HORIZON_STEPS, N_TARGETS)


def build_model(kind, lookback, n_past, n_future, n_spaces, hidden=64, layers=2, dropout=0.1):
    if kind == "linear":
        return LinearForecaster(lookback, n_past, n_future, n_spaces)
    if kind == "gru":
        return GRUForecaster(lookback, n_past, n_future, n_spaces, hidden, layers, dropout)
    raise ValueError(f"모르는 모델 종류: {kind}")


def count_parameters(model):
    return sum(p.numel() for p in model.parameters())


def masked_l1(pred, target, mask):
    """정답이 관측값인 칸만. co2·온도를 같은 비중으로 (항목별 평균의 평균)."""
    per_metric = []
    for j in range(target.shape[-1]):
        m = mask[..., j]
        total = m.sum()
        if total > 0:
            per_metric.append(((pred[..., j] - target[..., j]).abs() * m).sum() / total)
    if not per_metric:
        return pred.sum() * 0.0
    return torch.stack(per_metric).mean()
