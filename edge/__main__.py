"""과제 7: 엣지 최적화 측정. 보드 B에서 실행한다.

  python -m edge bench    가능한 실행 방식마다 지연(배치 1·4, 평균·p95)·결과 차이(원래 단위)·전력/GPU/메모리
  python -m edge report   docs/reports/007-comparison.md

실행 방식: PyTorch CPU(FP32 기준), PyTorch GPU(FP32·FP16), ONNX Runtime(가능한 제공자), TensorRT FP16.
설치돼 있지 않은 방식은 '없음'으로 기록하고 건너뛴다. 모델: 과제 3 예측(GRU), 과제 4 오토인코더.
과제 5 분류기는 트리 모델이라 TensorRT 대상이 아니다(보고서에 적음).
"""

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch

WARMUP, REPEAT = 20, 200


# ---------- 측정 도구 ----------

class Tegrastats:
    """tegrastats를 측정 동안 돌려 평균 전력·GPU 사용률·최대 RAM을 모은다 (없으면 빈 값)."""

    PATTERN_RAM = re.compile(r"RAM (\d+)/(\d+)MB")
    PATTERN_GPU = re.compile(r"GR3D_FREQ (\d+)%")
    PATTERN_POWER = re.compile(r"VDD_IN (\d+)mW")

    def __init__(self):
        self.path = shutil.which("tegrastats")
        self.lines, self.proc, self.thread = [], None, None

    def __enter__(self):
        if self.path:
            self.proc = subprocess.Popen([self.path, "--interval", "200"], stdout=subprocess.PIPE, text=True)
            self.thread = threading.Thread(target=lambda: [self.lines.append(l) for l in self.proc.stdout], daemon=True)
            self.thread.start()
        return self

    def __exit__(self, *exc):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    def summary(self):
        if not self.lines:
            return {"available": bool(self.path)}
        ram = [int(m.group(1)) for l in self.lines if (m := self.PATTERN_RAM.search(l))]
        gpu = [int(m.group(1)) for l in self.lines if (m := self.PATTERN_GPU.search(l))]
        power = [int(m.group(1)) for l in self.lines if (m := self.PATTERN_POWER.search(l))]
        return {"available": True, "samples": len(self.lines),
                "ram_max_mb": max(ram) if ram else None,
                "gpu_util_mean": float(np.mean(gpu)) if gpu else None,
                "power_mean_mw": float(np.mean(power)) if power else None}


def timed(fn, sync=None):
    for _ in range(WARMUP):
        fn()
    if sync:
        sync()
    times = []
    for _ in range(REPEAT):
        t0 = time.perf_counter()
        fn()
        if sync:
            sync()
        times.append((time.perf_counter() - t0) * 1000)
    return {"mean_ms": float(np.mean(times)), "p95_ms": float(np.percentile(times, 95)), "warmup": WARMUP, "repeat": REPEAT}


def environment():
    def run(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10).stdout.strip()
        except Exception:
            return ""
    info = {
        "machine": f"{platform.node()} {platform.system()} {platform.machine()}",
        "python": platform.python_version(), "torch": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
        "power_mode": run("nvpmodel -q 2>/dev/null | head -1") or "확인 불가",
        "jetson_clocks": "고정 안 함 (sudo 필요, 기본 동적 클럭)",
        "l4t": run("head -1 /etc/nv_tegra_release 2>/dev/null"),
        "threads": torch.get_num_threads(),
        "measured_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        import onnxruntime as ort
        info["onnxruntime"] = ort.__version__
        info["ort_providers"] = ort.get_available_providers()
    except ImportError:
        info["onnxruntime"] = None
    try:
        import tensorrt as trt
        info["tensorrt"] = trt.__version__
    except ImportError:
        info["tensorrt"] = None
    return info


# ---------- 모델과 입력 ----------

def forecast_model_and_inputs(root, n=2000):
    from forecast.dataset import samples_for
    from forecast.settings import load_forecast_settings
    from forecast.train import load_model

    fs = load_forecast_settings(root / "config" / "forecast.toml")
    chosen = json.loads((fs.selection_path).read_text(encoding="utf-8"))["chosen"]
    model, meta = load_model(fs.runs_dir / chosen)
    samples = samples_for(fs, meta["variant"], meta["target"], lookback=meta["lookback_steps"])
    pos = samples.positions["test"][:n]
    batch = samples.batch(pos)
    inputs = (batch["past"], batch["future"], batch["space"])
    std = samples.std[samples.space_id[pos]]                  # (n, 2) co2·온도 표준편차
    return chosen, model, inputs, ["past", "future", "space"], ["delta"], std


def autoencoder_model_and_inputs(root, n=2000):
    from anomaly.__main__ import Context
    ctx = Context(str(root / "config" / "anomaly.toml"))
    model = ctx.load_model()
    from anomaly.data import period_slice
    ds = ctx.dataset(period_slice(ctx.frames, *ctx.period("test")))
    x, m, c = ds.batch(ds.valid[:n])
    threshold = json.loads((ctx.out / "threshold.json").read_text(encoding="utf-8"))["threshold"]
    return "autoencoder", model, (x.numpy(), c.numpy()), ["x", "cond"], ["recon"], (m.numpy(), threshold)


# ---------- 실행 방식 ----------

def run_torch(model, inputs, device, half=False):
    model = model.to(device).eval()
    tensors = [torch.from_numpy(np.ascontiguousarray(a)).to(device) for a in inputs]
    if half:
        model = model.half()
        tensors = [t.half() for t in tensors]
    sync = torch.cuda.synchronize if device.type == "cuda" else None
    with torch.no_grad():
        out = torch.cat([model(*[t[i:i + 256] for t in tensors]).float().cpu() for i in range(0, len(tensors[0]), 256)]).numpy()
        lat = {f"batch{b}": timed(lambda b=b: model(*[t[:b] for t in tensors]), sync) for b in (1, 4)}
    model.float().cpu()
    return out, lat


def export_onnx(model, inputs, names, outputs, path):
    sample = tuple(torch.from_numpy(np.ascontiguousarray(a[:4])) for a in inputs)
    dynamic = {n: {0: "batch"} for n in names + outputs}
    torch.onnx.export(model.cpu().eval(), sample, path, input_names=names, output_names=outputs,
                      dynamic_axes=dynamic, opset_version=17, dynamo=False)
    return path


def run_onnxruntime(path, inputs, names, provider, fp16=False):
    import onnxruntime as ort

    providers = [provider] if provider == "CPUExecutionProvider" else [provider, "CPUExecutionProvider"]
    provider_options = None
    if provider == "TensorrtExecutionProvider":
        provider_options = [{"trt_fp16_enable": fp16, "trt_engine_cache_enable": True,
                             "trt_engine_cache_path": str(path.parent)}, {}]
    session = ort.InferenceSession(str(path), providers=providers, provider_options=provider_options)
    feed = lambda sl: {n: np.ascontiguousarray(a[sl]).astype(np.float32) for n, a in zip(names, inputs)}
    out = np.concatenate([session.run(None, feed(slice(i, i + 256)))[0] for i in range(0, len(inputs[0]), 256)])
    lat = {f"batch{b}": timed(lambda b=b: session.run(None, feed(slice(0, b)))) for b in (1, 4)}
    return out, lat


def run_tensorrt(path, inputs, names, outputs):
    """TensorRT FP16 엔진을 이 보드에서 빌드하고 실행 (torch CUDA 텐서를 버퍼로 씀)."""
    import tensorrt as trt

    logger = trt.Logger(trt.Logger.WARNING)
    builder = trt.Builder(logger)
    network = builder.create_network(0)
    parser = trt.OnnxParser(network, logger)
    if not parser.parse(path.read_bytes()):
        raise RuntimeError("; ".join(str(parser.get_error(i)) for i in range(parser.num_errors)))
    config = builder.create_builder_config()
    config.set_flag(trt.BuilderFlag.FP16)
    profile = builder.create_optimization_profile()
    for name, array in zip(names, inputs):
        rest = tuple(array.shape[1:])
        profile.set_shape(name, (1, *rest), (4, *rest), (256, *rest))
    config.add_optimization_profile(profile)
    engine_bytes = builder.build_serialized_network(network, config)
    (path.with_suffix(".engine")).write_bytes(engine_bytes)
    engine = trt.Runtime(logger).deserialize_cuda_engine(engine_bytes)
    context = engine.create_execution_context()
    stream = torch.cuda.Stream()

    def infer(batch_arrays):
        bs = batch_arrays[0].shape[0]
        tensors = [torch.from_numpy(np.ascontiguousarray(a)).cuda() for a in batch_arrays]
        for name, t in zip(names, tensors):
            context.set_input_shape(name, tuple(t.shape))
            context.set_tensor_address(name, t.data_ptr())
        out_shape = tuple(context.get_tensor_shape(outputs[0]))
        out = torch.empty(out_shape, dtype=torch.float32, device="cuda")
        context.set_tensor_address(outputs[0], out.data_ptr())
        context.execute_async_v3(stream.cuda_stream)
        stream.synchronize()
        return out

    result = np.concatenate([infer([a[i:i + 256] for a in inputs]).cpu().numpy() for i in range(0, len(inputs[0]), 256)])
    lat = {}
    for b in (1, 4):
        batch = [a[:b] for a in inputs]
        lat[f"batch{b}"] = timed(lambda batch=batch: infer(batch))
    return result, lat


# ---------- 비교 ----------

def forecast_diff(ref, out, std):
    """CO₂·온도 예측 차이 (원래 단위): 최대·평균."""
    diff = np.abs(ref - out) * std[:, None, :]
    return {"co2_max_ppm": float(diff[..., 0].max()), "co2_mean_ppm": float(diff[..., 0].mean()),
            "temp_max_c": float(diff[..., 1].max())}


def ae_diff(ref, out, extra, x):
    mask, threshold = extra
    tail = slice(-3, None)

    def score(recon):
        err = np.abs(recon - x) * mask
        per = (err[:, tail, :3] + err[:, tail, 3:]).sum(axis=1) / np.clip((mask[:, tail, :3] + mask[:, tail, 3:]).sum(axis=1), 1, None)
        present = mask[:, tail, :3].sum(axis=1) > 0
        return (per * present).sum(axis=1) / np.clip(present.sum(axis=1), 1, None)

    s_ref, s_out = score(ref), score(out)
    return {"score_max_diff": float(np.abs(s_ref - s_out).max()),
            "alarm_changed_rate": float(((s_ref > threshold) != (s_out > threshold)).mean())}


def bench_model(name, model, inputs, names, outputs, extra, out_dir, kind):
    results = {}
    with Tegrastats() as ts:
        ref, lat = run_torch(model, inputs, torch.device("cpu"))
    results["pytorch_cpu_fp32"] = {"latency": lat, "tegrastats": ts.summary(), "diff": "기준"}

    def compare(out):
        return forecast_diff(ref, out, extra) if kind == "forecast" else ae_diff(ref, out, extra, inputs[0])

    if torch.cuda.is_available():
        for half in (False, True):
            key = f"pytorch_gpu_{'fp16' if half else 'fp32'}"
            try:
                with Tegrastats() as ts:
                    out, lat = run_torch(model, inputs, torch.device("cuda"), half)
                results[key] = {"latency": lat, "tegrastats": ts.summary(), "diff": compare(out)}
            except Exception as exc:
                results[key] = {"error": str(exc)[:300]}
    else:
        results["pytorch_gpu"] = {"error": "CUDA판 PyTorch 없음"}
    onnx_path = export_onnx(model, inputs, names, outputs, out_dir / f"{name}.onnx")
    try:
        import onnxruntime as ort
        for provider in [p for p in ort.get_available_providers() if p != "AzureExecutionProvider"]:
            for fp16 in ((False, True) if provider == "TensorrtExecutionProvider" else (False,)):
                key = f"onnxruntime_{provider.replace('ExecutionProvider', '').lower()}" + ("_fp16" if fp16 else "")
                try:
                    with Tegrastats() as ts:
                        out, lat = run_onnxruntime(onnx_path, inputs, names, provider, fp16)
                    results[key] = {"latency": lat, "tegrastats": ts.summary(), "diff": compare(out)}
                except Exception as exc:
                    results[key] = {"error": str(exc)[:300]}
    except ImportError:
        results["onnxruntime"] = {"error": "onnxruntime 없음"}
    try:
        import tensorrt  # noqa: F401
        if not torch.cuda.is_available():
            raise ImportError("TensorRT 실행에 CUDA판 PyTorch(버퍼용)가 필요")
        with Tegrastats() as ts:
            out, lat = run_tensorrt(onnx_path, inputs, names, outputs)
        results["tensorrt_fp16"] = {"latency": lat, "tegrastats": ts.summary(), "diff": compare(out)}
    except ImportError as exc:
        results["tensorrt_fp16"] = {"error": f"TensorRT 없음: {exc}"}
    except Exception as exc:
        results["tensorrt_fp16"] = {"error": str(exc)[:300]}
    return results


def cmd_bench(root):
    out_dir = root / "data" / "edge"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = {"environment": environment(), "models": {}}
    print(json.dumps(report["environment"], ensure_ascii=False))
    for loader, kind in ((forecast_model_and_inputs, "forecast"), (autoencoder_model_and_inputs, "autoencoder")):
        try:
            name, model, inputs, names, outputs, extra = loader(root)
        except Exception as exc:
            report["models"][kind] = {"error": f"모델을 불러오지 못함: {exc}"}
            print(f"[{kind}] 건너뜀: {exc}")
            continue
        report["models"][name] = {"kind": kind, "samples": int(len(inputs[0])),
                                  "methods": bench_model(name, model, inputs, names, outputs, extra, out_dir, kind)}
        for method, r in report["models"][name]["methods"].items():
            if "latency" in r:
                print(f"[{name}] {method:<28} 배치1 {r['latency']['batch1']['mean_ms']:.3f} ms (p95 {r['latency']['batch1']['p95_ms']:.3f}), "
                      f"배치4 {r['latency']['batch4']['mean_ms']:.3f} ms, 차이 {r['diff']}")
            else:
                print(f"[{name}] {method:<28} {r.get('error')}")
    (out_dir / "bench.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def cmd_report(root):
    from pipeline.settings import load_settings

    settings = load_settings(root / "config" / "preprocess.toml", root=root)
    report = json.loads((root / "data" / "edge" / "bench.json").read_text(encoding="utf-8"))
    env = report["environment"]
    lines = [
        "# 과제 7 엣지 최적화 측정표",
        "",
        f"`python -m edge bench`가 만든 표. 측정 {env['measured_at']}. 해석은 [007-edge.md](007-edge.md).",
        "",
        "## 측정 조건",
        "",
        "| 항목 | 값 |", "|---|---|",
        f"| 장비 | {env['machine']}, {env.get('l4t', '')} |",
        f"| 전력 모드 | {env['power_mode']} |",
        f"| 클럭 | {env['jetson_clocks']} |",
        f"| 반복 | 워밍업 {WARMUP}회, 측정 {REPEAT}회, 배치 1(한 공간)·4(네 공간) |",
        f"| 소프트웨어 | Python {env['python']}, torch {env['torch']} (CUDA {env['cuda_available']}), onnxruntime {env.get('onnxruntime')} {env.get('ort_providers', '')}, TensorRT {env.get('tensorrt')} |",
        "| 결과 차이 | PyTorch CPU FP32 출력 대비, 시험 기간 표본 2,000개, 원래 단위 |",
        "",
    ]
    for name, entry in report["models"].items():
        if "methods" not in entry:
            lines += [f"## {name}", "", f"측정 못 함: {entry.get('error')}", ""]
            continue
        lines += [f"## {name} ({entry['kind']})", "",
                  "| 실행 방식 | 배치 1 평균 / p95 (ms) | 배치 4 평균 / p95 (ms) | 결과 차이 | 평균 전력 | GPU 사용률 | 최대 RAM |",
                  "|---|---:|---:|---|---:|---:|---:|"]
        for method, r in entry["methods"].items():
            if "latency" not in r:
                lines.append(f"| {method} | - | - | {r.get('error', '')} | - | - | - |")
                continue
            b1, b4, ts = r["latency"]["batch1"], r["latency"]["batch4"], r["tegrastats"]
            diff = r["diff"]
            if isinstance(diff, dict):
                diff = ", ".join(f"{k} {v:.4g}" for k, v in diff.items())
            power = f"{ts['power_mean_mw'] / 1000:.2f} W" if ts.get("power_mean_mw") else "-"
            gpu = f"{ts['gpu_util_mean']:.0f}%" if ts.get("gpu_util_mean") is not None else "-"
            ram = f"{ts['ram_max_mb']} MB" if ts.get("ram_max_mb") else "-"
            lines.append(f"| {method} | {b1['mean_ms']:.3f} / {b1['p95_ms']:.3f} | {b4['mean_ms']:.3f} / {b4['p95_ms']:.3f} | {diff} | {power} | {gpu} | {ram} |")
        lines.append("")
    lines += ["과제 5 분류기(HistGradientBoosting)는 트리 모델이라 TensorRT 변환 대상이 아니다. CPU에서 한 번 판정이 1 ms 안쪽이라 그대로 쓴다.", ""]
    (settings.reports_dir / "007-comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"측정표 → {settings.reports_dir / '007-comparison.md'}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m edge", description="과제 7 엣지 최적화 측정")
    parser.add_argument("command", choices=["bench", "report", "all"])
    parser.add_argument("--root", default=".")
    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    steps = {"bench": [cmd_bench], "report": [cmd_report], "all": [cmd_bench, cmd_report]}[args.command]
    for step in steps:
        code = step(root)
        if code:
            return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
