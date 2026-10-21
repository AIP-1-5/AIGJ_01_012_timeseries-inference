"""설정 파일(TOML)과 환경변수 읽기. 비밀값은 설정 파일에 두지 않고 환경변수에서만 읽는다."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_FIELDS = ("device_id", "space", "values")


class ConfigError(Exception):
    """설정 오류. CLI는 종료 코드 2로 끝낸다(systemd가 무한 재시작하지 않게)."""


@dataclass
class BrokerConfig:
    host: str
    port: int = 1883
    username_env: str = "MQTT_USERNAME"
    password_env: str = "MQTT_PASSWORD"
    client_id: str = ""
    keepalive_seconds: int = 30
    reconnect_min_seconds: int = 1
    reconnect_max_seconds: int = 20
    stale_after_minutes: float = 5
    qos: int = 1
    clean_session: bool = False

    def credentials(self):
        """(아이디, 비밀번호). 없으면 ConfigError. 오류 메시지에 값은 넣지 않는다."""
        username = os.environ.get(self.username_env)
        password = os.environ.get(self.password_env)
        missing = [name for name, value in ((self.username_env, username), (self.password_env, password)) if not value]
        if missing:
            raise ConfigError(f"환경변수가 비어 있다: {', '.join(missing)} (.env를 불러왔는지 확인)")
        return username, password


@dataclass
class SourceConfig:
    name: str
    topic: str
    format: str = "json"
    fields: dict = field(default_factory=dict)
    include: list = field(default_factory=list)
    rename: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)


@dataclass
class IntervalConfig:
    default: float = 1
    by_profile: dict = field(default_factory=dict)
    by_device: dict = field(default_factory=dict)

    def minutes_for(self, device_id, device_profile=None):
        if device_id in self.by_device:
            return float(self.by_device[device_id])
        if device_profile in self.by_profile:
            return float(self.by_profile[device_profile])
        return float(self.default)


@dataclass
class Config:
    path: Path
    broker: BrokerConfig
    storage_path: Path
    sources: list
    intervals: IntervalConfig


def _section(data, name, required=True):
    value = data.get(name)
    if value is None:
        if required:
            raise ConfigError(f"[{name}] 섹션이 없다")
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"[{name}]는 표(table)여야 한다")
    return value


def _source(raw, index):
    for key in ("name", "topic"):
        if not raw.get(key):
            raise ConfigError(f"sources[{index}].{key}가 없다")
    fields = raw.get("fields") or {}
    missing = [key for key in REQUIRED_FIELDS if not fields.get(key)]
    if missing:
        raise ConfigError(f"sources[{index}] ({raw['name']}): fields에 필수 키가 없다: {', '.join(missing)}")
    if raw.get("format", "json") != "json":
        raise ConfigError(f"sources[{index}] ({raw['name']}): 지원하는 format은 json뿐이다")
    metrics = raw.get("metrics") or {}
    return SourceConfig(
        name=raw["name"],
        topic=raw["topic"],
        format="json",
        fields=dict(fields),
        include=list(metrics.get("include", [])),
        rename=dict(metrics.get("rename", {})),
        units=dict(metrics.get("units", {})),
    )


def load_config(path):
    path = Path(path)
    try:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
    except FileNotFoundError as exc:
        raise ConfigError(f"설정 파일이 없다: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"설정 파일 문법 오류 ({path}): {exc}") from exc

    broker_raw = _section(data, "broker")
    if not broker_raw.get("host"):
        raise ConfigError("[broker] host가 없다")
    if not broker_raw.get("client_id"):
        raise ConfigError("[broker] client_id가 없다 (공용 계정이라 사람·보드마다 달라야 한다)")
    known = BrokerConfig.__dataclass_fields__
    unknown = sorted(set(broker_raw) - set(known))
    if unknown:
        raise ConfigError(f"[broker]에 알 수 없는 키: {', '.join(unknown)}")
    broker = BrokerConfig(**broker_raw)
    if broker.reconnect_max_seconds > 60:
        raise ConfigError("[broker] reconnect_max_seconds는 60 이하여야 한다 (복구 후 1분 안 재수집 기준)")

    storage_raw = _section(data, "storage")
    if not storage_raw.get("path"):
        raise ConfigError("[storage] path가 없다")
    storage_path = Path(storage_raw["path"])
    if not storage_path.is_absolute():
        storage_path = (Path.cwd() / storage_path).resolve()

    sources_raw = data.get("sources")
    if not sources_raw:
        raise ConfigError("[[sources]]가 하나도 없다")
    sources = [_source(raw, i) for i, raw in enumerate(sources_raw)]

    intervals_raw = _section(data, "intervals", required=False)
    intervals = IntervalConfig(
        default=float(intervals_raw.get("default", 1)),
        by_profile=dict(intervals_raw.get("by_profile", {})),
        by_device=dict(intervals_raw.get("by_device", {})),
    )
    return Config(path=path, broker=broker, storage_path=storage_path, sources=sources, intervals=intervals)
