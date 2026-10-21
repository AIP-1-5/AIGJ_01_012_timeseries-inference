"""모델 묶음 만들기 (보드 B에서). 학습 결과를 서비스가 읽는 형식으로 data/models/에 모은다.

  forecast.onnx + forecast.json     과제 3에서 고른 모델 (ONNX: 보드 A는 torch 없이 ONNX Runtime으로 돌린다)
  autoencoder.onnx + anomaly.json   과제 4 오토인코더, 임계값, 규칙 기준값
  state.pkl                         과제 5 분류기 두 개와 임계값
  manifest.json                     각 파일 sha256, 원본 실행, 만든 시각, 코드 커밋
"""

import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import torch

from pipeline.baselines import git_commit


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def export(root, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = {"created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "code_commit": git_commit(root), "files": {}}

    from forecast.settings import load_forecast_settings
    from forecast.train import load_model

    fs = load_forecast_settings(root / "config" / "forecast.toml")
    chosen = json.loads(fs.selection_path.read_text(encoding="utf-8"))["chosen"]
    model, meta = load_model(fs.runs_dir / chosen)
    sample = (torch.zeros(1, meta["lookback_steps"], meta["n_past"]), torch.zeros(1, 12, meta["n_future"]),
              torch.zeros(1, meta["n_spaces"]))
    torch.onnx.export(model, sample, out_dir / "forecast.onnx", input_names=["past", "future", "space"],
                      output_names=["delta"], dynamic_axes={n: {0: "batch"} for n in ("past", "future", "space", "delta")},
                      opset_version=17, dynamo=False)
    (out_dir / "forecast.json").write_text(json.dumps({**meta, "run_id": chosen}, ensure_ascii=False, indent=2), encoding="utf-8")

    anomaly_dir = root / "data" / "anomaly"
    checkpoint = torch.load(anomaly_dir / "autoencoder.pt", map_location="cpu", weights_only=False)
    from anomaly.model import AutoEncoder

    ae = AutoEncoder(checkpoint["window"], checkpoint["n_cond"], checkpoint["hidden"], checkpoint["bottleneck"])
    ae.load_state_dict(checkpoint["state_dict"])
    ae.eval()
    torch.onnx.export(ae, (torch.zeros(1, checkpoint["window"], 6), torch.zeros(1, checkpoint["n_cond"])),
                      out_dir / "autoencoder.onnx", input_names=["x", "cond"], output_names=["recon"],
                      dynamic_axes={n: {0: "batch"} for n in ("x", "cond", "recon")}, opset_version=17, dynamo=False)
    threshold = json.loads((anomaly_dir / "threshold.json").read_text(encoding="utf-8"))
    rules = json.loads((anomaly_dir / "rules.json").read_text(encoding="utf-8"))
    (out_dir / "anomaly.json").write_text(json.dumps({
        "window": checkpoint["window"], "n_cond": checkpoint["n_cond"], "spaces": checkpoint["spaces"],
        "threshold": threshold["threshold"], "threshold_decided_at": threshold["decided_at"], "rules": rules,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    shutil.copy(root / "data" / "state" / "models.pkl", out_dir / "state.pkl")
    shutil.copy(root / "artifacts" / "normalization.json", out_dir / "normalization.json")
    for path in sorted(out_dir.iterdir()):
        if path.name != "manifest.json":
            manifest["files"][path.name] = sha256(path)
    manifest["forecast_run"] = chosen
    import sklearn

    manifest["versions"] = {"scikit-learn": sklearn.__version__, "torch": torch.__version__, "onnx_opset": 17}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest
