"""기준선 3종과 평가 시점 (research.md R10, 완료 조건 2·3).

예측 시각 t에서 h분 뒤를 맞힌다 (h = 5, 15, 30, 60).
  현재값 유지:       ŷ(t+h) = y(t)
  최근 1시간 평균:   ŷ(t+h) = mean(y(t−55분) … y(t))   (12칸 중 9칸 이상 있을 때)
  어제 같은 시각:    ŷ(t+h) = y(t+h−24시간)

평가 시점(artifacts/eval_points.csv)은 한 번 고정하고 과제 3·6 모델도 같은 파일로 평가한다.
h마다 쓸 수 있는 t를 열(h5, h15, …)로 표시한다. 같은 h에서는 모든 기준선·모델이 같은 시점 집합으로 채점된다.
h마다 따로 표시하는 이유: 회의실 센서는 10분 간격이라 5·15분 뒤 칸에는 관측 정답이 있을 수 없다.
"""

import json
import math
import platform
import subprocess
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .preprocess import clean_path, load_clean
from .schedule import holiday_dates
from .split import PERIOD_LABELS, PERIODS, assign_period, load_split, split_path

BASELINES = ("persistence", "mean_1h", "yesterday")
BASELINE_LABELS = {"persistence": "현재값 유지", "mean_1h": "최근 1시간 평균", "yesterday": "어제 같은 시각"}
UNITS = {"co2": "ppm", "temperature": "℃", "humidity": "%"}
METRIC_LABELS = {"co2": "CO₂", "temperature": "온도", "humidity": "습도"}
BASE_COLUMNS = ["space", "metric", "time", "weekday_class_hours"]


def horizon_column(horizon):
    return f"h{horizon}"


def point_columns(settings):
    return BASE_COLUMNS + [horizon_column(h) for h in settings.horizons]


def regular_series(clean, space, metric, interval_minutes):
    """한 공간·항목을 빈틈없는 격자로 (shift가 곧 시간 이동이 되도록)."""
    frame = clean[(clean["space"] == space) & (clean["metric"] == metric)].set_index("time").sort_index()
    if frame.empty:
        return frame
    grid = pd.date_range(frame.index.min(), frame.index.max(), freq=f"{interval_minutes}min")
    frame = frame.reindex(grid)
    frame["interpolated"] = frame["interpolated"].fillna(False).astype(bool)
    frame["fault"] = frame["fault"].fillna("")
    return frame


def masks(frame):
    """observed: 정답으로 쓸 수 있는 칸. usable: 입력으로 쓸 수 있는 칸(보간 허용, 고장 제외)."""
    usable = frame["value"].notna() & frame["fault"].eq("")
    observed = usable & ~frame["interpolated"]
    return observed, usable


def class_hours_flags(times, settings, country="KR"):
    """평일(공휴일 제외) class_hours 안이면 True (부분집합 보고용)."""
    local = times.tz_convert(settings.timezone)
    off = holiday_dates(country, set(local.year))
    start, end = settings.class_hours
    clock = local.hour * 60 + local.minute
    weekday = (local.weekday < 5) & ~pd.Index(local.date).isin(off)
    return np.asarray(weekday & (clock >= start.hour * 60 + start.minute) & (clock < end.hour * 60 + end.minute))


def build_eval_points(clean, split, settings):
    step = settings.interval_minutes
    window = settings.mean_window_minutes // step
    need = math.ceil(window * settings.min_window_fraction)
    day = 24 * 60 // step
    test_start, test_end = split["test"]["start"], split["test"]["end"]
    parts = []
    for spec in settings.spaces:
        for metric in settings.targets:
            if metric not in spec.metrics:
                continue
            frame = regular_series(clean, spec.space, metric, step)
            if frame.empty:
                continue
            observed, usable = masks(frame)
            ok = observed & (usable.astype(int).rolling(window, min_periods=window).sum() >= need)
            times = frame.index.to_series()
            ok &= (times >= test_start) & (times < test_end)
            flags = {}
            for horizon in settings.horizons:
                k = horizon // step
                flags[horizon_column(horizon)] = (
                    ok
                    & observed.shift(-k, fill_value=False)                # 정답 y(t+h)가 관측값
                    & usable.shift(day - k, fill_value=False)             # y(t+h−24h)가 있음
                    & ((times + pd.Timedelta(minutes=horizon)) < test_end)  # 정답도 시험 기간 안
                )
            flags = pd.DataFrame(flags)
            keep = flags.any(axis=1).to_numpy()
            part = flags[keep].reset_index(drop=True)
            part.insert(0, "time", frame.index[keep])
            part.insert(0, "metric", metric)
            part.insert(0, "space", spec.space)
            parts.append(part)
    if not parts:
        return pd.DataFrame(columns=point_columns(settings))
    points = pd.concat(parts, ignore_index=True)
    points["weekday_class_hours"] = class_hours_flags(pd.DatetimeIndex(points["time"]), settings) if len(points) else []
    return points[point_columns(settings)]


def save_points(points, path):
    out = points.copy()
    out["time"] = pd.DatetimeIndex(out["time"]).strftime("%Y-%m-%dT%H:%M:%SZ")
    for column in out.columns[3:]:
        out[column] = out[column].astype(int)          # 0/1로 저장 (파일 크기)
    out.to_csv(path, index=False)


def load_points(path):
    points = pd.read_csv(path, dtype={"space": str, "metric": str}, keep_default_na=False)
    points["time"] = pd.to_datetime(points["time"], utc=True, format="ISO8601")
    for column in points.columns[3:]:
        points[column] = points[column].astype(str).str.lower().isin(["1", "true"])
    return points


def predict(clean, points, settings):
    """평가 시점마다 기준선 3종 × h의 예측과 정답. 열: space, metric, time, horizon_min, baseline, y_true, y_pred."""
    step = settings.interval_minutes
    window = settings.mean_window_minutes // step
    day = 24 * 60 // step
    parts = []
    for (space, metric), group in points.groupby(["space", "metric"], sort=False):
        frame = regular_series(clean, space, metric, step)
        observed, usable = masks(frame)
        value_in = frame["value"].where(usable)
        truth_all = frame["value"].where(observed)
        mean_1h = value_in.rolling(window, min_periods=1).mean()
        for horizon in settings.horizons:
            k = horizon // step
            column = horizon_column(horizon)
            if column not in group or not group[column].any():
                continue
            rows = group[group[column]]
            at = pd.DatetimeIndex(rows["time"])
            truth = truth_all.shift(-k).reindex(at).to_numpy()
            preds = {
                "persistence": value_in.reindex(at).to_numpy(),
                "mean_1h": mean_1h.reindex(at).to_numpy(),
                "yesterday": value_in.shift(day - k).reindex(at).to_numpy(),
            }
            for name in BASELINES:
                parts.append(pd.DataFrame({
                    "space": space, "metric": metric, "time": at, "horizon_min": horizon,
                    "baseline": name, "y_true": truth, "y_pred": preds[name],
                    "weekday_class_hours": rows["weekday_class_hours"].to_numpy(),
                }))
    return pd.concat(parts, ignore_index=True)


def mae_table(predictions, subset=None):
    """공간별 + 전체(공간 합침) MAE와 평가 시점 수."""
    frame = predictions if subset is None else predictions[predictions[subset]]
    frame = frame.assign(abs_error=(frame["y_pred"] - frame["y_true"]).abs())
    per_space = frame.groupby(["metric", "space", "baseline", "horizon_min"], sort=False)["abs_error"].agg(["mean", "size"])
    overall = frame.groupby(["metric", "baseline", "horizon_min"], sort=False)["abs_error"].agg(["mean", "size"])
    overall = overall.assign(space="전체").set_index("space", append=True).reorder_levels(per_space.index.names)
    table = pd.concat([per_space, overall]).rename(columns={"mean": "mae", "size": "n"}).reset_index()
    return table


def strongest(table):
    """항목 × h마다 전체 MAE가 가장 작은 기준선."""
    overall = table[table["space"] == "전체"]
    best = overall.loc[overall.groupby(["metric", "horizon_min"])["mae"].idxmin()]
    return best.sort_values(["metric", "horizon_min"]).reset_index(drop=True)


def git_commit(root):
    try:
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True, text=True,
                                check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "pipeline", "config"], cwd=root,
                               capture_output=True, text=True).stdout.strip()
        return commit + (" (커밋 안 된 수정 있음)" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "확인 불가"


def conditions_block(settings, clean, split, extra=None):
    """모든 결과표 위에 붙이는 측정 조건 (constitution 원칙 IV)."""
    tz = settings.timezone
    fmt = "%Y-%m-%d %H:%M"
    lines = [
        "| 항목 | 값 |",
        "|---|---|",
        f"| 데이터 | 실데이터 (교육장 InfluxDB `{settings.bucket}`), 합성 아님 |",
        f"| 데이터 기간 | {clean['time'].min().tz_convert(tz):{fmt}} ~ {clean['time'].max().tz_convert(tz):{fmt}} KST (5분 칸 끝 시각) |",
    ]
    for period in PERIODS:
        start, end = split[period]["start"], split[period]["end"]
        lines.append(f"| {PERIOD_LABELS[period]} 기간 | {start:%Y-%m-%d} ~ {end:%Y-%m-%d} KST (끝 미포함, {(end - start).days}일) |")
    lines += [
        f"| 칸 | {settings.interval_minutes}분, [t−{settings.interval_minutes}분, t) → t |",
        f"| 실행 환경 | {platform.system()} {platform.machine()}, Python {platform.python_version()} (분석은 보드 밖에서, 전력 모드 해당 없음) |",
        f"| 코드·설정 커밋 | `{git_commit(settings.root)}` (`{settings.path.name}`) |",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"| {key} | {value} |")
    return "\n".join(lines)


def format_mae_tables(table, settings, title_suffix=""):
    out = []
    horizons = settings.horizons
    for metric in settings.targets:
        part = table[table["metric"] == metric]
        if part.empty:
            continue
        unit = UNITS.get(metric, "")
        out.append(f"#### {METRIC_LABELS.get(metric, metric)} MAE ({unit}){title_suffix}\n")
        out.append("| 공간 | 기준선 | " + " | ".join(f"{h}분" for h in horizons) + " | 평가 시점 수 (" + "/".join(f"{h}" for h in horizons) + "분) |")
        out.append("|---|---|" + "---:|" * len(horizons) + "---:|")
        spaces = [s.space for s in settings.spaces if s.space in set(part["space"])] + ["전체"]
        for space in spaces:
            rows = part[part["space"] == space]
            if rows.empty:
                continue
            best = {h: rows[rows["horizon_min"] == h]["mae"].min() for h in horizons}
            for name in BASELINES:
                cells, counts = [], []
                for h in horizons:
                    hit = rows[(rows["baseline"] == name) & (rows["horizon_min"] == h)]
                    if hit.empty:
                        cells.append("-")
                        counts.append("0")
                        continue
                    mae = hit["mae"].iloc[0]
                    counts.append(f"{int(hit['n'].iloc[0]):,}")
                    text = f"{mae:.2f}" if metric != "co2" else f"{mae:.1f}"
                    cells.append(f"**{text}**" if np.isclose(mae, best[h]) else text)
                label = f"**{space}**" if space == "전체" else space
                out.append(f"| {label} | {BASELINE_LABELS[name]} | " + " | ".join(cells) + f" | {' / '.join(counts)} |")
        out.append("")
    return "\n".join(out)


def baselines_markdown(settings, clean, split, points, table, table_class, notes):
    best = strongest(table)
    lines = [
        "# 과제 2 기준선 성능표",
        "",
        f"`python -m pipeline baselines`가 만든 표. 만든 시각 {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC.",
        "해석은 [002-diagnosis.md](002-diagnosis.md)에 쓴다.",
        "",
        "## 측정 조건",
        "",
        conditions_block(settings, clean, split, {
            "평가 시점": f"`artifacts/eval_points.csv` {len(points):,}개 (평일 실습 시간 {int(points['weekday_class_hours'].sum()):,}개)",
            "평가 규칙": "시험 기간의 t 중 y(t) 관측, 직전 1시간 12칸 중 9칸 이상, 그리고 h마다 y(t+h−24h) 있음·정답 y(t+h)가 관측값(보간·고장 아님). 같은 h에서는 세 기준선이 같은 시점",
            "빈칸(-)": "그 h에 관측 정답이 없다. 회의실은 10분 간격 센서라 5·15분 뒤 칸이 항상 보간이다",
        }),
        "",
        "굵은 글씨 = 그 공간·그 h에서 MAE가 가장 작은 기준선. "
        "'전체'는 그 h의 모든 공간 평가 시점을 합친 값이라, 회의실이 들어가는 30·60분과 빠지는 5·15분은 공간 구성이 다르다(같은 h 안의 기준선 비교는 공정하다).",
        "",
        "## 전체 평가 시점",
        "",
        format_mae_tables(table, settings),
        "## 평일 실습 시간만 (09:00~18:00, 공휴일 제외)",
        "",
        "밤·주말은 값이 거의 안 변해서 어느 기준선이든 맞히기 쉽다. 사람이 드나드는 시간만 따로 본다.",
        "",
        format_mae_tables(table_class, settings, " — 평일 실습 시간"),
        "## 가장 강한 기준선 (전체 MAE 기준, 자동 집계)",
        "",
        "| 항목 | h | 가장 강한 기준선 | MAE | 현재값 유지 대비 |",
        "|---|---:|---|---:|---:|",
    ]
    overall = table[table["space"] == "전체"].set_index(["metric", "baseline", "horizon_min"])["mae"]
    for row in best.itertuples():
        ref = overall.get((row.metric, "persistence", row.horizon_min), float("nan"))
        ratio = row.mae / ref if ref else float("nan")
        lines.append(f"| {METRIC_LABELS.get(row.metric, row.metric)} | {row.horizon_min}분 | {BASELINE_LABELS[row.baseline]} | "
                     f"{row.mae:.2f} {UNITS.get(row.metric, '')} | {ratio:.2f}배 |")
    if notes:
        lines += ["", "## 참고", ""] + [f"- {note}" for note in notes]
    return "\n".join(lines) + "\n"


def points_path(settings):
    return settings.artifacts_dir / "eval_points.csv"


def run(settings, rebuild_points=False):
    clean = load_clean(clean_path(settings))
    if not split_path(settings).exists():
        print("artifacts/split.json이 없다. 먼저 split을 실행한다")
        return 1
    split = load_split(split_path(settings))
    path = points_path(settings)
    notes = []
    if path.exists() and not rebuild_points:
        points = load_points(path)
        print(f"평가 시점: 고정된 목록 사용 ({path}, {len(points):,}개)")
    else:
        points = build_eval_points(clean, split, settings)
        settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        save_points(points, path)
        print(f"평가 시점: 새로 만듦 → {path} ({len(points):,}개)")
    if points.empty:
        print("평가 시점이 없다. 시험 기간 데이터와 결측률을 확인한다")
        return 1

    predictions = predict(clean, points, settings)
    invalid = predictions["y_true"].isna() | predictions["y_pred"].isna()
    if invalid.any():
        keys = ["space", "metric", "time", "horizon_min"]
        bad_points = predictions.loc[invalid, keys].drop_duplicates()
        notes.append(f"고정된 평가 시점 중 {len(bad_points):,}개(시점×h)는 지금 데이터로 정답·예측을 만들 수 없어 뺐다 "
                     "(전처리 규칙이 바뀌었으면 `--rebuild-points`와 이유 기록)")
        predictions = predictions[~predictions.set_index(keys).index.isin(bad_points.set_index(keys).index)]
    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    out = predictions.copy()
    out["time"] = pd.DatetimeIndex(out["time"]).strftime("%Y-%m-%dT%H:%M:%SZ")
    out.to_csv(settings.processed_dir / "baseline_predictions.csv.gz", index=False, float_format="%.4f",
               compression={"method": "gzip", "compresslevel": 1})

    table = mae_table(predictions)
    table_class = mae_table(predictions, subset="weekday_class_hours")
    table.to_csv(settings.processed_dir / "baseline_mae.csv", index=False)
    table_class.to_csv(settings.processed_dir / "baseline_mae_class_hours.csv", index=False)
    flag_columns = [horizon_column(h) for h in settings.horizons]
    counts = points.groupby(["space", "metric"])[flag_columns].sum()
    print("평가 시점 수 (" + "/".join(f"{h}" for h in settings.horizons) + "분):")
    for (space, metric), row in counts.iterrows():
        print(f"  {space:<8} {metric:<12} " + " / ".join(f"{int(v):,}" for v in row))
        missing = [str(h) for h, v in zip(settings.horizons, row) if v == 0]
        if missing:
            notes.append(f"{space} {METRIC_LABELS.get(metric, metric)}: {', '.join(missing)}분 뒤는 평가 시점이 없다 "
                         "(10분 간격 센서라 그 칸은 항상 보간 → 관측 정답이 없음)")

    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    report = settings.reports_dir / "002-baselines.md"
    report.write_text(baselines_markdown(settings, clean, split, points, table, table_class, notes), encoding="utf-8")
    print(f"성능표 → {report}")
    for row in strongest(table).itertuples():
        print(f"  {METRIC_LABELS.get(row.metric, row.metric)} {row.horizon_min:>2}분: 가장 강한 기준선 = "
              f"{BASELINE_LABELS[row.baseline]} (MAE {row.mae:.2f} {UNITS.get(row.metric, '')})")
    summary = {"points": int(len(points)), "strongest": strongest(table).to_dict(orient="records")}
    (settings.processed_dir / "baselines_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str),
                                                                  encoding="utf-8")
    return 0
