"""과제 6: 평가 경로 정합성 (가짜 모델 = 현재값 유지면 MAE가 같아야 한다)."""

import json
from argparse import Namespace

import numpy as np
import pandas as pd
import torch

from pipeline.settings import load_settings
from pretrained import __main__ as pm
from tests.test_forecast import make_root


class LastValue:
    """마지막 값을 그대로 내는 가짜 Chronos (구간 ±50)."""

    def predict_quantiles(self, ctx, prediction_length, quantile_levels):
        last = torch.tensor([row[~torch.isnan(row)][-1].item() for row in ctx])
        median = last[:, None].repeat(1, prediction_length)
        q = torch.stack([median - 50, median, median + 50], dim=-1)
        return q, median


def test_pretrained_path_matches_persistence(tmp_path, monkeypatch):
    root = make_root(tmp_path, None)
    monkeypatch.setattr(pm, "load_pipeline", lambda model_id, device="cpu": LastValue())
    args = Namespace(models=["fake/chronos-x"], contexts=[24, 48], device="cpu", root=str(root))
    assert pm.cmd_predict(args, root) == 0
    selection = json.loads((root / "data" / "pretrained" / "selection.json").read_text(encoding="utf-8"))
    assert selection["models"]["fake/chronos-x"]["chosen_context"] in (24, 48)
    assert pm.cmd_report(args, root) == 0
    s = load_settings(root / "config" / "preprocess.toml", root=root)
    base = pd.read_csv(s.processed_dir / "baseline_predictions.csv.gz")
    fake = pd.read_csv(root / "data" / "pretrained" / "chronos-x-L24.csv.gz")
    p = base[(base["baseline"] == "persistence") & (base["metric"] == "co2")]
    for h in (5, 60):
        a = (p[p["horizon_min"] == h]["y_pred"] - p[p["horizon_min"] == h]["y_true"]).abs().mean()
        b = (fake[fake["horizon_min"] == h]["y_pred"] - fake[fake["horizon_min"] == h]["y_true"]).abs().mean()
        assert np.isclose(a, b, atol=1e-3)
        assert len(p[p["horizon_min"] == h]) == len(fake[fake["horizon_min"] == h])
    report = (root / "docs" / "reports" / "006-comparison.md").read_text(encoding="utf-8")
    assert "예측 구간 적중률" in report and "현재값 유지" in report
