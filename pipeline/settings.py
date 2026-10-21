"""설정 파일 읽기: config/preprocess.toml, config/schedule.toml (contracts/config.md).

비밀값(InfluxDB 토큰)은 설정 파일에 두지 않고 환경변수에서만 읽는다.
"""

import os
import tomllib
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path

DEFAULT_CONFIG = "config/preprocess.toml"
DEFAULT_SCHEDULE = "config/schedule.toml"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
EVENT_KINDS = ("start", "partial", "end")


class SettingsError(Exception):
    """설정 오류. CLI는 종료 코드 2로 끝낸다."""


@dataclass
class SpaceSpec:
    space: str
    slug: str
    metrics: dict                       # 항목 → "all" 또는 센서 ID 목록
    exclude_devices: list = field(default_factory=list)


@dataclass
class Settings:
    path: Path
    root: Path
    timezone: str
    bucket: str
    source_metrics: list
    source_start: str
    interval_minutes: int
    max_interp_minutes: int
    duplicate_seconds: float
    ranges: dict                        # 항목 → (최소, 최대)
    stuck_minutes: dict                 # 항목 → 분
    night_co2_hours: tuple
    night_co2_excess_ppm: float
    spaces: list
    test_days: int
    val_days: int
    boundary_hour: int
    horizons: list
    targets: list
    mean_window_minutes: int
    min_window_fraction: float
    class_hours: tuple

    @property
    def analysis_metrics(self):
        """정제·분석하는 항목 (공간 설정에 나온 항목, 설정 순서)."""
        seen = []
        for spec in self.spaces:
            for metric in spec.metrics:
                if metric not in seen:
                    seen.append(metric)
        return seen

    def space(self, name):
        for spec in self.spaces:
            if spec.space == name:
                return spec
        return None

    def slug(self, name):
        spec = self.space(name)
        return spec.slug if spec else name

    # 파일 위치 (저장소 루트 기준)
    @property
    def raw_dir(self):
        return self.root / "data" / "raw" / "influx"

    @property
    def processed_dir(self):
        return self.root / "data" / "processed"

    @property
    def artifacts_dir(self):
        return self.root / "artifacts"

    @property
    def reports_dir(self):
        return self.root / "docs" / "reports"


@dataclass
class ScheduleEvent:
    time: time
    kind: str
    label: str


@dataclass
class SpaceSchedule:
    space: str
    weekdays: list                      # 0=월 … 6=일
    events: list


@dataclass
class Schedule:
    path: Path
    source: str
    holidays: str
    baseline_minutes: int
    threshold_ppm: float
    window_minutes: int
    morning_baseline: tuple
    active_hours: tuple
    min_rise_ppm: float
    min_coverage: float
    spaces: list

    def for_space(self, name):
        for spec in self.spaces:
            if spec.space == name:
                return spec
        return None


def parse_hhmm(text, where):
    try:
        hour, minute = str(text).split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        raise SettingsError(f"{where}: 시각은 \"HH:MM\" 형식이어야 한다 (받은 값: {text!r})") from None


def _read_toml(path):
    path = Path(path)
    if not path.exists():
        raise SettingsError(f"설정 파일이 없다: {path}")
    try:
        with path.open("rb") as fp:
            return tomllib.load(fp)
    except tomllib.TOMLDecodeError as exc:
        raise SettingsError(f"{path}: TOML 형식 오류: {exc}") from None


def _require(data, key, where):
    if key not in data:
        raise SettingsError(f"{where}에 {key}가 없다")
    return data[key]


def _hour_pair(value, where):
    if not isinstance(value, list) or len(value) != 2:
        raise SettingsError(f"{where}: [\"HH:MM\", \"HH:MM\"] 형식이어야 한다")
    start, end = (parse_hhmm(v, where) for v in value)
    if start >= end:
        raise SettingsError(f"{where}: 시작이 끝보다 앞서야 한다")
    return start, end


def repo_root_for(config_path):
    """config/preprocess.toml → 저장소 루트."""
    path = Path(config_path).resolve()
    return path.parent.parent if path.parent.name == "config" else Path.cwd()


def load_settings(path=DEFAULT_CONFIG, root=None):
    path = Path(path)
    data = _read_toml(path)
    source = data.get("source", {})
    resample = _require(data, "resample", path.name)
    faults = data.get("faults", {})
    split = _require(data, "split", path.name)
    evaluation = _require(data, "evaluation", path.name)

    ranges = {}
    for metric, pair in _require(data, "ranges", path.name).items():
        if not isinstance(pair, list) or len(pair) != 2 or not pair[0] < pair[1]:
            raise SettingsError(f"[ranges] {metric}: [최소, 최대]이고 최소 < 최대여야 한다")
        ranges[metric] = (float(pair[0]), float(pair[1]))

    spaces = []
    names = set()
    for i, raw in enumerate(_require(data, "spaces", path.name)):
        where = f"[[spaces]] {i + 1}번째"
        name = _require(raw, "space", where)
        if name in names:
            raise SettingsError(f"{where}: 공간 {name}이 두 번 나온다")
        names.add(name)
        metrics = {}
        for key, rule in raw.items():
            if key in ("space", "slug", "exclude_devices"):
                continue
            if rule != "all" and not (isinstance(rule, list) and all(isinstance(d, str) for d in rule)):
                raise SettingsError(f"{where} {key}: \"all\" 또는 센서 ID 목록이어야 한다")
            if key not in ranges:
                raise SettingsError(f"{where} {key}: [ranges]에 허용 범위가 없다")
            metrics[key] = rule
        if not metrics:
            raise SettingsError(f"{where}: 분석할 항목이 없다")
        spaces.append(SpaceSpec(name, raw.get("slug", name), metrics, list(raw.get("exclude_devices", []))))

    stuck = faults.get("stuck_minutes", 60)
    metrics_all = {m for spec in spaces for m in spec.metrics}
    stuck_minutes = dict(stuck) if isinstance(stuck, dict) else {m: stuck for m in metrics_all}

    interval = int(_require(resample, "interval_minutes", "[resample]"))
    if interval <= 0 or 60 % interval:
        raise SettingsError("[resample] interval_minutes는 60을 나누는 양수여야 한다")
    horizons = [int(h) for h in _require(evaluation, "horizons_minutes", "[evaluation]")]
    if any(h <= 0 or h % interval for h in horizons):
        raise SettingsError("[evaluation] horizons_minutes는 interval_minutes의 배수여야 한다")
    targets = list(_require(evaluation, "targets", "[evaluation]"))
    for metric in targets:
        if metric not in metrics_all:
            raise SettingsError(f"[evaluation] targets의 {metric}을 분석하는 공간이 없다")

    return Settings(
        path=path,
        root=Path(root) if root else repo_root_for(path),
        timezone=data.get("timezone", "Asia/Seoul"),
        bucket=source.get("influx_bucket", "iot-bucket"),
        source_metrics=list(source.get("metrics", sorted(metrics_all))),
        source_start=str(source.get("start", "2025-08-08")),
        interval_minutes=interval,
        max_interp_minutes=int(resample.get("max_interp_minutes", 15)),
        duplicate_seconds=float(resample.get("duplicate_seconds", 10)),
        ranges=ranges,
        stuck_minutes={m: float(v) for m, v in stuck_minutes.items()},
        night_co2_hours=_hour_pair(faults.get("night_co2_hours", ["02:00", "05:00"]), "[faults] night_co2_hours"),
        night_co2_excess_ppm=float(faults.get("night_co2_excess_ppm", 150)),
        spaces=spaces,
        test_days=int(_require(split, "test_days", "[split]")),
        val_days=int(_require(split, "val_days", "[split]")),
        boundary_hour=int(split.get("boundary_hour", 0)),
        horizons=horizons,
        targets=targets,
        mean_window_minutes=int(evaluation.get("mean_window_minutes", 60)),
        min_window_fraction=float(evaluation.get("min_window_fraction", 0.75)),
        class_hours=_hour_pair(evaluation.get("class_hours", ["09:00", "18:00"]), "[evaluation] class_hours"),
    )


def load_schedule(path=DEFAULT_SCHEDULE):
    path = Path(path)
    data = _read_toml(path)
    response = data.get("response", {})
    mismatch = data.get("mismatch", {})
    spaces = []
    for i, raw in enumerate(_require(data, "spaces", path.name)):
        where = f"{path.name} [[spaces]] {i + 1}번째"
        days = []
        for day in raw.get("weekdays", WEEKDAYS[:5]):
            if day not in WEEKDAYS:
                raise SettingsError(f"{where}: 요일은 {', '.join(WEEKDAYS)} 중 하나 (받은 값: {day})")
            days.append(WEEKDAYS.index(day))
        events = []
        for event in _require(raw, "events", where):
            kind = event.get("kind")
            if kind not in EVENT_KINDS:
                raise SettingsError(f"{where}: kind는 {', '.join(EVENT_KINDS)} 중 하나 (받은 값: {kind})")
            events.append(ScheduleEvent(parse_hhmm(event.get("time"), where), kind, event.get("label", kind)))
        events.sort(key=lambda e: e.time)
        spaces.append(SpaceSchedule(_require(raw, "space", where), days, events))
    return Schedule(
        path=path,
        source=data.get("source", "출처 미상"),
        holidays=data.get("holidays", "KR"),
        baseline_minutes=int(response.get("baseline_minutes", 30)),
        threshold_ppm=float(response.get("threshold_ppm", 30)),
        window_minutes=int(response.get("window_minutes", 120)),
        morning_baseline=_hour_pair(mismatch.get("morning_baseline", ["08:00", "09:00"]), "[mismatch] morning_baseline"),
        active_hours=_hour_pair(mismatch.get("active_hours", ["09:00", "18:00"]), "[mismatch] active_hours"),
        min_rise_ppm=float(mismatch.get("min_rise_ppm", 100)),
        min_coverage=float(mismatch.get("min_coverage", 0.5)),
        spaces=spaces,
    )


@dataclass
class InfluxEnv:
    url: str
    token: str
    org: str
    bucket: str


def influx_env(default_bucket):
    """InfluxDB 접속 정보 (.env → 환경변수). 오류 메시지에 값은 넣지 않는다."""
    values = {name: os.environ.get(name, "") for name in ("INFLUX_URL", "INFLUX_TOKEN", "INFLUX_ORG")}
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise SettingsError(f"환경변수가 비어 있다: {', '.join(missing)} (./pipeline.sh로 실행해 .env를 불러왔는지 확인)")
    return InfluxEnv(
        url=values["INFLUX_URL"].rstrip("/"),
        token=values["INFLUX_TOKEN"],
        org=values["INFLUX_ORG"],
        bucket=os.environ.get("INFLUX_BUCKET") or default_bucket,
    )
