"""기간 분할과 정규화 값 (research.md R7·R8, 완료 조건 1).

시간 순서로 학습 → 검증 → 시험. 시험 = 데이터 끝(KST 자정)에서 연속 test_days일, 검증 = 그 앞 val_days일,
학습 = 나머지. 무작위로 섞으면 이웃한 5분 칸(거의 같은 값)이 학습과 시험에 나뉘어 시험 성적이 부푼다.

정규화 값(평균·표준편차)은 학습 기간의 관측값(보간·고장 칸 제외)으로만 계산한다.
분할은 artifacts/split.json에 고정하고 과제 3~8이 같은 파일을 쓴다.
"""

import json
from datetime import datetime, timezone

import pandas as pd

from .preprocess import clean_path, load_clean

PERIODS = ("train", "val", "test")
PERIOD_LABELS = {"train": "학습", "val": "검증", "test": "시험"}


def make_split(clean, settings):
    tz = settings.timezone
    times = clean.loc[clean["value"].notna(), "time"]
    if times.empty:
        raise ValueError("값이 있는 칸이 없다")
    first = times.min().tz_convert(tz).floor("D") + pd.Timedelta(hours=settings.boundary_hour)
    end = times.max().tz_convert(tz).floor("D") + pd.Timedelta(hours=settings.boundary_hour)
    test_start = end - pd.Timedelta(days=settings.test_days)
    val_start = test_start - pd.Timedelta(days=settings.val_days)
    if val_start - first < pd.Timedelta(days=settings.val_days):
        raise ValueError(f"데이터가 짧아 학습 기간이 검증 기간({settings.val_days}일)보다 짧다: {first:%Y-%m-%d} ~ {end:%Y-%m-%d}")
    return {
        "timezone": tz,
        "train": {"start": first, "end": val_start},
        "val": {"start": val_start, "end": test_start},
        "test": {"start": test_start, "end": end},
        "data_end": end,
    }


def split_to_json(split, settings):
    def iso(ts):
        return ts.isoformat()

    return {
        "timezone": split["timezone"],
        **{p: {"start": iso(split[p]["start"]), "end": iso(split[p]["end"])} for p in PERIODS},
        "data_end": iso(split["data_end"]),
        "rule": (f"시간 순서. 시험 = 데이터 끝(KST {settings.boundary_hour:02d}시)에서 연속 {settings.test_days}일, "
                 f"검증 = 그 앞 {settings.val_days}일, 학습 = 나머지. 끝 시각은 포함하지 않는다. 칸 시각(칸 끝)으로 판정"),
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def load_split(path):
    data = json.loads(open(path, encoding="utf-8").read())
    split = {"timezone": data["timezone"], "data_end": pd.Timestamp(data["data_end"])}
    for period in PERIODS:
        split[period] = {"start": pd.Timestamp(data[period]["start"]), "end": pd.Timestamp(data[period]["end"])}
    return split


def assign_period(times, split):
    """시각마다 "train" / "val" / "test" / "" (분할 밖)."""
    labels = pd.Series("", index=times.index, dtype=object)
    for period in PERIODS:
        inside = (times >= split[period]["start"]) & (times < split[period]["end"])
        labels[inside] = period
    return labels


def observed_mask(clean):
    """정답·통계에 쓸 수 있는 칸: 값이 있고, 보간이 아니고, 고장 표시가 없다."""
    return clean["value"].notna() & ~clean["interpolated"] & clean["fault"].eq("")


def compute_normalization(clean, split):
    """공간·항목별 평균·표준편차. 학습 기간 관측값만 쓴다 (검증·시험 값은 결과에 영향이 없다)."""
    train = clean[(assign_period(clean["time"], split) == "train") & observed_mask(clean)]
    values = {}
    for (space, metric), group in train.groupby(["space", "metric"], sort=True):
        values.setdefault(space, {})[metric] = {
            "mean": round(float(group["value"].mean()), 6),
            "std": round(float(group["value"].std(ddof=0)), 6),
            "n": int(len(group)),
        }
    return values


def normalization_to_json(values, split):
    return {
        "computed_from": "train",
        "period": {"start": split["train"]["start"].isoformat(), "end": split["train"]["end"].isoformat()},
        "rule": "학습 기간의 관측 칸(보간·고장 제외)만 사용. 표준편차는 모집단 기준(ddof=0)",
        "values": values,
    }


def load_normalization(path):
    return json.loads(open(path, encoding="utf-8").read())


def normalize(values, space, metric, normalization):
    stats = normalization["values"][space][metric]
    return (values - stats["mean"]) / stats["std"]


def denormalize(values, space, metric, normalization):
    stats = normalization["values"][space][metric]
    return values * stats["std"] + stats["mean"]


def period_summary(clean, split):
    """기간 × 공간 × 항목의 관측·결측 비율 (시험 기간에 결측·고장이 몰렸는지 보려고)."""
    frame = clean.assign(period=assign_period(clean["time"], split), observed=observed_mask(clean),
                         missing=clean["value"].isna(), faulty=clean["fault"].ne(""))
    frame = frame[frame["period"] != ""]
    return (frame.groupby(["period", "space", "metric"], sort=False)
            .agg(slots=("time", "size"), observed=("observed", "mean"), missing=("missing", "mean"),
                 fault=("faulty", "mean"), interpolated=("interpolated", "mean"))
            .reset_index())


def split_path(settings):
    return settings.artifacts_dir / "split.json"


def normalization_path(settings):
    return settings.artifacts_dir / "normalization.json"


def run(settings, force=False):
    clean = load_clean(clean_path(settings))
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = split_path(settings)
    if path.exists() and not force:
        split = load_split(path)
        print(f"분할: 이미 고정됨, 그대로 사용 ({path}). 다시 만들려면 --force (이유를 진단서에 적는다)")
    else:
        split = make_split(clean, settings)
        path.write_text(json.dumps(split_to_json(split, settings), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"분할: 새로 만듦 → {path}")
    for period in PERIODS:
        start, end = split[period]["start"], split[period]["end"]
        print(f"  {PERIOD_LABELS[period]}: {start:%Y-%m-%d %H:%M} ~ {end:%Y-%m-%d %H:%M} KST ({(end - start).days}일)")

    values = compute_normalization(clean, split)
    normalization_path(settings).write_text(
        json.dumps(normalization_to_json(values, split), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n정규화 값 (학습 기간 관측값만) → {normalization_path(settings)}")
    for space, metrics in values.items():
        for metric, stats in metrics.items():
            print(f"  {space:<8} {metric:<12} 평균 {stats['mean']:>9.2f}  표준편차 {stats['std']:>8.2f}  n={stats['n']:,}")

    summary = period_summary(clean, split)
    print("\n기간별 관측 비율 (시험 기간에 결측·고장이 몰리면 경계를 옮기고 이유를 적는다)")
    table = summary.pivot_table(index=["space", "metric"], columns="period", values="observed", sort=False)
    for (space, metric), row in table.iterrows():
        cells = "  ".join(f"{PERIOD_LABELS[p]} {row.get(p, float('nan')):6.1%}" for p in PERIODS)
        print(f"  {space:<8} {metric:<12} {cells}")
    return 0
