"""정제 → 5분 칸 → 공간 중앙값 → 짧은 결측 보간 → 고장 표시 (research.md R4·R5·R6·R9).

과제 3~8과 상시 서비스가 같은 함수를 쓴다 (constitution 원칙 III). 같은 입력이면 같은 출력이다.

칸 규칙: [t−5분, t)의 값으로 시각 t의 칸을 만든다. 시각 t에는 t 이전에 측정된 값만 들어간다.
"""

import json

import numpy as np
import pandas as pd

from .loader import load_metric, raw_files

CLEAN_COLUMNS = ["time", "space", "metric", "value", "n_raw", "n_sensors", "interpolated", "fault"]
STAT_COLUMNS = [
    "space", "metric", "sensors", "raw_rows", "excluded_device_rows", "duplicates", "removed_out_of_range",
    "stuck_raw_rows", "slots_total", "slots_observed", "slots_interpolated", "slots_missing", "slots_fault",
]
FAULT_STUCK = "stuck"


def bin_label(times, interval_minutes):
    """칸 [t−interval, t)의 끝 시각 t. 정각 값(09:05:00)은 다음 칸(09:10)에 들어간다."""
    return times.dt.floor(f"{interval_minutes}min") + pd.Timedelta(minutes=interval_minutes)


def select_rows(raw, metric, settings):
    """설정에 있는 공간·센서만 남긴다. (남은 행, 공간별 센서 제외 행 수)"""
    frame = raw[raw["metric"].astype(str) == metric]
    space = frame["space"].astype(str)
    device = frame["device_id"].astype(str)
    keep = pd.Series(False, index=frame.index)
    excluded = {}
    for spec in settings.spaces:
        if metric not in spec.metrics:
            continue
        rule = spec.metrics[metric]
        allowed = ~device.isin(spec.exclude_devices)
        if rule != "all":
            allowed &= device.isin(rule)
        in_space = space.eq(spec.space)
        keep |= in_space & allowed
        excluded[spec.space] = int((in_space & ~allowed).sum())
    out = frame[keep].copy()
    out["space"] = out["space"].astype(str)
    out["device_id"] = out["device_id"].astype(str)
    return out, excluded


def mark_stuck(frame, stuck_minutes, gap_minutes):
    """센서별로 같은 값이 stuck_minutes 이상 이어진 원시 행에 True. 끊긴 구간(gap 초과)은 이어 보지 않는다."""
    if frame.empty:
        return pd.Series(False, index=frame.index)
    ordered = frame.sort_values(["device_id", "time"], kind="mergesort")
    device_changed = ordered["device_id"].ne(ordered["device_id"].shift())
    value_changed = ordered["value"].ne(ordered["value"].shift())
    gap = ordered["time"].diff() > pd.Timedelta(minutes=gap_minutes)
    run = (device_changed | value_changed | gap).cumsum()
    times = ordered.groupby(run)["time"]
    span = times.transform("max") - times.transform("min")
    return (span >= pd.Timedelta(minutes=stuck_minutes)).reindex(frame.index)


def clean_metric(raw, metric, settings):
    """한 항목: 원시 → 공간별 5분 칸(격자 맞추기 전). (칸 표, 공간별 집계, 센서 목록)"""
    lo, hi = settings.ranges[metric]
    frame, excluded = select_rows(raw, metric, settings)
    stats = {space: {"raw_rows": 0, "excluded_device_rows": n, "duplicates": 0, "removed_out_of_range": 0,
                     "stuck_raw_rows": 0, "sensors": 0} for space, n in excluded.items()}
    for space, count in frame.groupby("space").size().items():
        stats[space]["raw_rows"] = int(count) + stats[space]["excluded_device_rows"]

    before = frame.groupby("space").size()
    frame = frame.sort_values(["device_id", "time"], kind="mergesort").drop_duplicates(["device_id", "time"])
    for space, count in (before - frame.groupby("space").size()).fillna(0).items():
        stats[space]["duplicates"] = int(count)

    in_range = frame["value"].between(lo, hi)
    for space, count in (~in_range).groupby(frame["space"]).sum().items():
        stats[space]["removed_out_of_range"] = int(count)
    frame = frame[in_range].copy()

    frame["stuck"] = mark_stuck(frame, settings.stuck_minutes.get(metric, 60), settings.max_interp_minutes)
    for space, count in frame.groupby("space")["stuck"].sum().items():
        stats[space]["stuck_raw_rows"] = int(count)

    devices = (frame.groupby(["space", "device_id"])["time"].agg(["size", "min", "max"]).reset_index()
               .rename(columns={"size": "rows", "min": "first", "max": "last"}))
    devices["metric"] = metric
    for space, count in devices.groupby("space").size().items():
        stats[space]["sensors"] = int(count)

    frame["time"] = bin_label(frame["time"], settings.interval_minutes)
    sensor_bins = (frame.groupby(["space", "device_id", "time"], sort=True)
                   .agg(value=("value", "mean"), n_raw=("value", "size"), stuck=("stuck", "any")).reset_index())
    good = sensor_bins[~sensor_bins["stuck"]]
    bad = sensor_bins[sensor_bins["stuck"]]
    agg = {"value": ("value", "median"), "n_raw": ("n_raw", "sum"), "n_sensors": ("device_id", "size")}
    ok = good.groupby(["space", "time"]).agg(**agg)
    ok["fault"] = ""
    stuck_only = bad.groupby(["space", "time"]).agg(**agg)
    stuck_only = stuck_only[~stuck_only.index.isin(ok.index)]
    stuck_only["fault"] = FAULT_STUCK
    slots = pd.concat([ok, stuck_only]).sort_index().reset_index()
    slots["metric"] = metric
    return slots, stats, devices


def fill_short_gaps(values, fault, max_slots):
    """양쪽이 정상 관측인 max_slots 칸 이하 결측만 선형 보간. (채운 값, 보간 표시)"""
    missing = values.isna()
    run_id = (~missing).cumsum()
    run_len = missing.groupby(run_id).transform("sum")
    is_fault = fault.ne("")
    known_fault = is_fault.where(~missing)
    prev_fault = known_fault.ffill().fillna(True).astype(bool)
    next_fault = known_fault.bfill().fillna(True).astype(bool)
    interpolated = values.interpolate(method="linear", limit_area="inside")
    fill = missing & (run_len <= max_slots) & interpolated.notna() & ~prev_fault & ~next_fault
    return values.where(~fill, interpolated), fill


def to_grid(slots, start, end, settings):
    """공간·항목마다 start~end의 빠짐없는 5분 격자에 놓고 짧은 결측을 보간한다."""
    grid = pd.date_range(start, end, freq=f"{settings.interval_minutes}min", tz="UTC")
    max_slots = settings.max_interp_minutes // settings.interval_minutes
    parts = []
    for (space, metric), group in slots.groupby(["space", "metric"], sort=False):
        series = group.set_index("time").reindex(grid)
        series.index.name = "time"
        series["fault"] = series["fault"].fillna("")
        series["n_raw"] = series["n_raw"].fillna(0).astype(int)
        series["n_sensors"] = series["n_sensors"].fillna(0).astype(int)
        series["value"], series["interpolated"] = fill_short_gaps(series["value"], series["fault"], max_slots)
        series["space"], series["metric"] = space, metric
        parts.append(series.reset_index())
    return pd.concat(parts, ignore_index=True)[CLEAN_COLUMNS]


def order_frame(clean, settings):
    space_order = {spec.space: i for i, spec in enumerate(settings.spaces)}
    metric_order = {m: i for i, m in enumerate(settings.analysis_metrics)}
    key = clean["space"].map(space_order) * 100 + clean["metric"].map(metric_order)
    return clean.assign(_k=key).sort_values(["_k", "time"], kind="mergesort").drop(columns="_k").reset_index(drop=True)


def preprocess(raw_by_metric, settings):
    """전처리 전체. raw_by_metric: {항목: 원시 표}. (정제 표, 집계 표, 센서 목록)"""
    slot_parts, stat_rows, device_parts = [], [], []
    for metric in settings.analysis_metrics:
        raw = raw_by_metric.get(metric)
        if raw is None or raw.empty:
            continue
        slots, stats, devices = clean_metric(raw, metric, settings)
        slot_parts.append(slots)
        device_parts.append(devices)
        for space, values in stats.items():
            stat_rows.append({"space": space, "metric": metric, **values})
    slots = pd.concat(slot_parts, ignore_index=True) if slot_parts else pd.DataFrame()
    if slots.empty:
        raise ValueError("설정한 공간·항목에 해당하는 원시 데이터가 없다 (공간 이름과 InfluxDB location 태그가 같은지 확인)")
    start, end = slots["time"].min(), slots["time"].max()
    clean = order_frame(to_grid(slots, start, end, settings), settings)
    stats = pd.DataFrame(stat_rows)
    counts = clean.assign(
        observed=clean["value"].notna() & ~clean["interpolated"] & clean["fault"].eq(""),
        missing=clean["value"].isna(),
        faulty=clean["fault"].ne(""),
    ).groupby(["space", "metric"]).agg(
        slots_total=("time", "size"), slots_observed=("observed", "sum"), slots_interpolated=("interpolated", "sum"),
        slots_missing=("missing", "sum"), slots_fault=("faulty", "sum"),
    ).reset_index()
    stats = stats.merge(counts, on=["space", "metric"], how="left").fillna(0)
    for column in STAT_COLUMNS[2:]:
        stats[column] = stats[column].astype(int)
    stats = order_frame(stats.assign(time=0), settings).drop(columns="time")[STAT_COLUMNS]
    devices = pd.concat(device_parts, ignore_index=True) if device_parts else pd.DataFrame()
    return clean, stats, devices


def save_clean(clean, path):
    out = clean.copy()
    out["time"] = out["time"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(path, index=False, float_format="%.4f")


def load_clean(path):
    clean = pd.read_csv(path, keep_default_na=False, na_values={"value": [""]},
                        dtype={"space": str, "metric": str, "fault": str})
    clean["time"] = pd.to_datetime(clean["time"], utc=True, format="ISO8601")
    clean["interpolated"] = clean["interpolated"].astype(str).str.lower().eq("true")
    clean["value"] = clean["value"].astype(float)
    return clean


def clean_path(settings):
    return settings.processed_dir / "clean_5min.csv.gz"


def unconfigured_spaces(raw_by_metric, settings):
    """원시 데이터에 있지만 설정에 없는 공간과 행 수 (분석하지 않는다는 것을 진단서에 남기려고)."""
    configured = {spec.space for spec in settings.spaces}
    counts = {}
    for raw in raw_by_metric.values():
        for space, count in raw["space"].astype(str).value_counts().items():
            if space not in configured:
                counts[space] = counts.get(space, 0) + int(count)
    return dict(sorted(counts.items(), key=lambda item: -item[1]))


def run(settings):
    raw_by_metric = {}
    for metric in settings.analysis_metrics:
        files = raw_files(settings.raw_dir, metric)
        if not files:
            print(f"원시 파일이 없다: {settings.raw_dir / metric} (보드 B에서 export-influx 후 scp로 가져온다)")
            return 1
        raw_by_metric[metric] = load_metric(settings.raw_dir, metric)
        print(f"읽기: {metric} {len(files)}개 파일, {len(raw_by_metric[metric]):,}행")

    clean, stats, devices = preprocess(raw_by_metric, settings)
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    save_clean(clean, clean_path(settings))
    stats.to_csv(settings.processed_dir / "clean_stats.csv", index=False)
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    devices_out = devices.assign(first=devices["first"].dt.strftime(fmt), last=devices["last"].dt.strftime(fmt))
    meta = {
        "data_start": clean["time"].min().strftime(fmt),
        "data_end": clean["time"].max().strftime(fmt),
        "raw_rows": {m: int(len(r)) for m, r in raw_by_metric.items()},
        "unconfigured_spaces": unconfigured_spaces(raw_by_metric, settings),
        "devices": devices_out.to_dict(orient="records"),
    }
    (settings.processed_dir / "preprocess_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n정제 결과: {meta['data_start']} ~ {meta['data_end']} (UTC), {len(clean):,}칸 → {clean_path(settings)}")
    print(f"{'공간':<8} {'항목':<12} {'센서':>4} {'범위 밖':>8} {'중복':>6} {'관측':>7} {'보간':>7} {'결측':>7} {'고장':>6}")
    for row in stats.itertuples():
        total = max(row.slots_total, 1)
        print(f"{row.space:<8} {row.metric:<12} {row.sensors:>4} {row.removed_out_of_range:>8,} {row.duplicates:>6,} "
              f"{row.slots_observed / total:>7.1%} {row.slots_interpolated / total:>7.1%} "
              f"{row.slots_missing / total:>7.1%} {row.slots_fault / total:>6.1%}")
    if meta["unconfigured_spaces"]:
        listed = ", ".join(f"{s or '(빈칸)'} {n:,}행" for s, n in list(meta["unconfigured_spaces"].items())[:8])
        print(f"\n설정에 없어 분석하지 않은 공간: {listed}")
    return 0
