"""시각 변환. 저장은 모두 UTC ISO 8601 문자열(밀리초, 끝에 Z)로 한다."""

import re
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

_FRACTION = re.compile(r"(\.\d{1,6})\d*")


def parse_time(text):
    """ISO 8601 문자열을 aware datetime으로. 소수점 7자리 이상(nsTime)은 6자리로 자른다."""
    if not isinstance(text, str) or not text:
        return None
    value = _FRACTION.sub(r"\1", text.strip(), count=1)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def to_iso(dt):
    """aware datetime을 저장용 문자열로: 2026-10-21T00:31:12.103Z"""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def from_iso(text):
    return parse_time(text)


def utcnow():
    return datetime.now(timezone.utc)


def to_kst_str(dt, fmt="%Y-%m-%d %H:%M:%S"):
    return dt.astimezone(KST).strftime(fmt)
