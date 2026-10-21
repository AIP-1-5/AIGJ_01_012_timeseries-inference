"""config/forecast.toml 읽기. 과제 2 설정(config/preprocess.toml, config/schedule.toml)과 경로를 같이 쓴다."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from pipeline.settings import SettingsError, load_schedule, load_settings

DEFAULT_CONFIG = "config/forecast.toml"
VARIANTS = ("full", "no_schedule", "sensor_only")
KINDS = ("linear", "gru")
TARGETS = ("delta", "value")
HORIZON_STEPS = 12                     # 5분 × 12 = 60분
TARGET_METRICS = ("co2", "temperature")
INPUT_METRICS = ("co2", "temperature", "humidity")


@dataclass
class ForecastSettings:
    path: Path
    lookback_minutes: int
    min_window_fraction: float
    kind: str
    hidden: int
    layers: int
    dropout: float
    target: str
    seeds: list
    batch_size: int
    learning_rate: float
    max_epochs: int
    patience: int
    device: str
    variants: list
    pipeline: object = field(repr=False, default=None)      # pipeline.settings.Settings
    schedule: object = field(repr=False, default=None)      # pipeline.settings.Schedule

    @property
    def lookback_steps(self):
        return self.lookback_minutes // self.pipeline.interval_minutes

    @property
    def out_dir(self):
        return self.pipeline.root / "data" / "forecast"

    @property
    def runs_dir(self):
        return self.out_dir / "runs"

    @property
    def predictions_dir(self):
        return self.out_dir / "predictions"

    @property
    def selection_path(self):
        return self.out_dir / "selection.json"

    @property
    def reports_dir(self):
        return self.pipeline.reports_dir


def load_forecast_settings(path=DEFAULT_CONFIG, pipeline_config=None, schedule_config=None, root=None):
    path = Path(path)
    if not path.exists():
        raise SettingsError(f"설정 파일이 없다: {path}")
    with path.open("rb") as fp:
        try:
            data = tomllib.load(fp)
        except tomllib.TOMLDecodeError as exc:
            raise SettingsError(f"{path}: TOML 형식 오류: {exc}") from None
    config_dir = path.resolve().parent
    pipeline = load_settings(pipeline_config or config_dir / "preprocess.toml", root=root)
    schedule = load_schedule(schedule_config or config_dir / "schedule.toml")
    d, m, t, v = (data.get(k, {}) for k in ("data", "model", "train", "variants"))
    settings = ForecastSettings(
        path=path,
        lookback_minutes=int(d.get("lookback_minutes", 120)),
        min_window_fraction=float(d.get("min_window_fraction", 0.75)),
        kind=str(m.get("kind", "gru")),
        hidden=int(m.get("hidden", 64)),
        layers=int(m.get("layers", 2)),
        dropout=float(m.get("dropout", 0.1)),
        target=str(m.get("target", "delta")),
        seeds=[int(s) for s in t.get("seeds", [1, 2, 3])],
        batch_size=int(t.get("batch_size", 512)),
        learning_rate=float(t.get("learning_rate", 1e-3)),
        max_epochs=int(t.get("max_epochs", 30)),
        patience=int(t.get("patience", 5)),
        device=str(t.get("device", "cpu")),
        variants=list(v.get("names", list(VARIANTS))),
        pipeline=pipeline,
        schedule=schedule,
    )
    if settings.lookback_minutes <= 0 or settings.lookback_minutes % pipeline.interval_minutes:
        raise SettingsError(f"[data] lookback_minutes는 {pipeline.interval_minutes}분의 배수여야 한다")
    if settings.kind not in KINDS:
        raise SettingsError(f"[model] kind는 {', '.join(KINDS)} 중 하나")
    if settings.target not in TARGETS:
        raise SettingsError(f"[model] target은 {', '.join(TARGETS)} 중 하나")
    bad = [name for name in settings.variants if name not in VARIANTS]
    if bad:
        raise SettingsError(f"[variants] 모르는 변형: {', '.join(bad)} (가능: {', '.join(VARIANTS)})")
    if pipeline.interval_minutes * HORIZON_STEPS != 60:
        raise SettingsError("과제 3은 5분 칸 × 12 = 60분을 가정한다")
    return settings
