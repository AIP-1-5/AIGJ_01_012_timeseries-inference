"""과제 7: 맥에서 만든 묶음만으로 보드 측정이 돈다 (보드는 1년치 데이터로 표본을 만들지 않는다)."""

import json
import shutil
from pathlib import Path

import numpy as np
import torch

from anomaly.model import AutoEncoder
from forecast.model import build_model

ROOT = Path(__file__).resolve().parent.parent


def make_bundle(root, n=32):
    out = root / "data" / "edge" / "bundle"
    out.mkdir(parents=True)
    torch.manual_seed(0)
    gru = build_model("gru", 24, 12, 6, 4)
    with torch.no_grad():
        for p in gru.parameters():
            p.add_(torch.randn_like(p) * 0.05)                 # 0 초기화가 아니게 (차이를 잴 수 있도록)
    torch.save(gru, out / "forecast.pt")
    rng = np.random.default_rng(0)
    np.savez_compressed(out / "forecast.npz", in_past=rng.normal(size=(n, 24, 12)).astype(np.float32),
                        in_future=rng.normal(size=(n, 12, 6)).astype(np.float32),
                        in_space=np.eye(4, dtype=np.float32)[rng.integers(0, 4, n)], std=np.full((n, 2), 100.0, np.float32))
    ae = AutoEncoder(12, 7, 16, 4)
    torch.save(ae, out / "autoencoder.pt")
    np.savez_compressed(out / "autoencoder.npz", in_x=rng.normal(size=(n, 12, 6)).astype(np.float32),
                        in_cond=rng.normal(size=(n, 7)).astype(np.float32), mask=np.ones((n, 12, 6), bool))
    (out / "index.json").write_text(json.dumps([
        {"kind": "forecast", "name": "gru-test", "names": ["past", "future", "space"], "outputs": ["delta"]},
        {"kind": "autoencoder", "name": "autoencoder", "names": ["x", "cond"], "outputs": ["recon"], "threshold": 1.0},
    ]), encoding="utf-8")


def test_bench_from_bundle_only(tmp_path, monkeypatch):
    from edge import __main__ as edge

    (tmp_path / "config").mkdir()
    shutil.copy(ROOT / "config" / "preprocess.toml", tmp_path / "config" / "preprocess.toml")
    make_bundle(tmp_path)
    monkeypatch.setattr(edge, "REPEAT", 5)
    monkeypatch.setattr(edge, "WARMUP", 1)
    monkeypatch.setattr(edge, "forecast_model_and_inputs", lambda root: (_ for _ in ()).throw(AssertionError("데이터를 읽으면 안 됨")))
    assert edge.main(["all", "--root", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "data" / "edge" / "bench.json").read_text(encoding="utf-8"))
    methods = report["models"]["gru-test"]["methods"]
    assert "latency" in methods["pytorch_cpu_fp32"]
    ort_cpu = methods["onnxruntime_cpu"]["diff"]
    assert ort_cpu["co2_max_ppm"] < 1.0                          # ONNX 변환 차이 1 ppm 이하
    ae = report["models"]["autoencoder"]["methods"]
    assert ae["onnxruntime_cpu"]["diff"]["alarm_changed_rate"] <= 0.001
    assert "gru-test" in (tmp_path / "docs" / "reports" / "007-comparison.md").read_text(encoding="utf-8")
