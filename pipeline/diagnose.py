"""데이터 진단: 결측률, 긴 빈칸, 고장 의심, 하루 패턴, 일정 반응, 어긋난 날 (research.md R9·R11).

패턴·일정 분석은 학습+검증 기간만 쓴다. 시험 기간을 보고 설계를 정하면 시험이 더는 처음 보는 데이터가 아니다.
결측률·고장 같은 데이터 품질은 시험 기간도 함께 본다 (시험 기간에 문제가 몰렸는지 확인하려고).
"""

import json
import logging

import numpy as np
import pandas as pd

from .preprocess import clean_path, load_clean
from .schedule import class_day_flags, day_rises, event_times, mismatched_days, response_times
from .split import assign_period, load_split, observed_mask, period_summary, split_path

# 그래프 색 (dataviz 기본 팔레트: 범주 1·2번, 글자·면 토큰)
SURFACE = "#fcfcfb"
TEXT = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
SERIES = ["#2a78d6", "#eb6834", "#1baf7a"]
KOREAN_FONTS = ["AppleGothic", "Apple SD Gothic Neo", "NanumGothic", "Noto Sans CJK KR", "Noto Sans KR",
                "Malgun Gothic", "WenQuanYi Zen Hei"]
METRIC_LABELS = {"co2": "CO₂ (ppm)", "temperature": "온도 (℃)", "humidity": "습도 (%)"}
OUTDOOR_CO2 = 420


def runs(mask):
    """True가 이어진 구간들: (시작 위치, 끝 위치(포함)) 목록."""
    values = np.asarray(mask, dtype=bool)
    if not values.any():
        return []
    edges = np.diff(np.concatenate([[0], values.astype(int), [0]]))
    return list(zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1) - 1))


def longest_runs(frame, mask, settings, top=5):
    """가장 긴 구간 top개: 시작·끝(KST)과 길이(분)."""
    times = frame["time"]
    step = pd.Timedelta(minutes=settings.interval_minutes)
    found = []
    for start, end in runs(mask):
        begin = times.iloc[start] - step                 # 칸 시각은 칸 끝이므로 구간 시작 = 첫 칸 − 5분
        found.append({"start": begin.tz_convert(settings.timezone).strftime("%Y-%m-%d %H:%M"),
                      "end": times.iloc[end].tz_convert(settings.timezone).strftime("%Y-%m-%d %H:%M"),
                      "minutes": int((end - start + 1) * settings.interval_minutes)})
    return sorted(found, key=lambda r: -r["minutes"])[:top]


def quality(clean, stats, split, settings):
    """결측률 표(기간별), 긴 빈칸, 고장 의심 구간."""
    summary = period_summary(clean, split)
    gaps, faults = {}, {}
    for (space, metric), group in clean.groupby(["space", "metric"], sort=False):
        group = group.reset_index(drop=True)
        key = f"{space}|{metric}"
        gaps[key] = longest_runs(group, group["value"].isna(), settings)
        faults[key] = longest_runs(group, group["fault"].ne(""), settings)
    return {
        "stats": stats.to_dict(orient="records"),
        "by_period": summary.round(4).to_dict(orient="records"),
        "longest_gaps": gaps,
        "stuck_episodes": {k: v for k, v in faults.items() if v},
    }


def night_drift(clean, settings):
    """밤(02~05시) CO₂ 최솟값이 외기+excess보다 높은 밤 = 영점 틀어짐 의심 (표시만)."""
    start, end = settings.night_co2_hours
    co2 = clean[(clean["metric"] == "co2") & observed_mask(clean)]
    local = co2["time"].dt.tz_convert(settings.timezone)
    clock = local.dt.hour * 60 + local.dt.minute
    night = co2[(clock > start.hour * 60 + start.minute) & (clock <= end.hour * 60 + end.minute)]
    night = night.assign(night=night["time"].dt.tz_convert(settings.timezone).dt.date)
    limit = OUTDOOR_CO2 + settings.night_co2_excess_ppm
    result = {}
    for space, group in night.groupby("space", sort=False):
        floors = group.groupby("night")["value"].min()
        flagged = floors[floors > limit]
        streak = max((e - s + 1 for s, e in runs((floors > limit).to_numpy())), default=0)
        result[space] = {
            "nights": int(len(floors)), "flagged": int(len(flagged)),
            "flagged_rate": round(len(flagged) / len(floors), 4) if len(floors) else 0.0,
            "median_floor": round(float(floors.median()), 1) if len(floors) else None,
            "longest_streak": int(streak), "limit": limit,
            "examples": [str(d) for d in flagged.index[:10]],
        }
    return result


def daily_profile(clean, settings, schedule, mask_period):
    """시계(5분)별 평균·표준편차, 일정 있는 날 / 없는 날로 나눠서. 학습+검증만.

    보간 칸도 넣는다. 10분 센서는 계절마다 측정이 들어오는 칸(정각대/5분대)이 바뀌어서,
    측정 칸만 쓰면 이웃 칸이 서로 다른 계절의 평균이 되어 그래프가 톱니 모양이 된다. 고장 칸은 뺀다.
    """
    frame = clean[mask_period & clean["value"].notna() & clean["fault"].eq("")]
    local = frame["time"].dt.tz_convert(settings.timezone) - pd.Timedelta(minutes=settings.interval_minutes)
    frame = frame.assign(day=local.dt.date, clock=local.dt.hour + local.dt.minute / 60)  # 칸 시작 시각 기준
    profiles = {}
    for (space, metric), group in frame.groupby(["space", "metric"], sort=False):
        lab = schedule.for_space(space)
        if lab is not None:
            flags = class_day_flags(sorted(set(group["day"])), lab, schedule)
            kind = group["day"].map(flags)
        else:
            kind = pd.Series([d.weekday() < 5 for d in group["day"]], index=group.index)
        profile = group.assign(kind=kind.map({True: "on", False: "off"})) \
            .groupby(["kind", "clock"])["value"].agg(["mean", "std", "size"]).reset_index()
        profiles[(space, metric)] = (profile, lab is not None)
    return profiles


def profile_facts(profiles):
    """하루 패턴에서 숫자로 뽑을 수 있는 관찰 (진단서 자동 제안용)."""
    facts = []
    for (space, metric), (profile, has_schedule) in profiles.items():
        on = profile[profile["kind"] == "on"].set_index("clock")["mean"]
        off = profile[profile["kind"] == "off"].set_index("clock")["mean"]
        if on.empty:
            continue
        night = on[(on.index >= 2) & (on.index < 5)]
        facts.append({
            "space": space, "metric": metric, "schedule": has_schedule,
            "peak_clock": float(on.idxmax()), "peak": round(float(on.max()), 2),
            "night_mean": round(float(night.mean()), 2) if len(night) else None,
            "day_range": round(float(on.max() - on.min()), 2),
            "off_day_range": round(float(off.max() - off.min()), 2) if len(off) else None,
            "at_14_on": round(float(on.get(14.0, np.nan)), 2), "at_14_off": round(float(off.get(14.0, np.nan)), 2),
        })
    return facts


def schedule_analysis(clean, settings, schedule, mask_period):
    """실습실 일정 반응 시간과 어긋난 날. 학습+검증만."""
    out = {"source": schedule.source, "spaces": {}}
    for lab in schedule.spaces:
        frame = clean[(clean["space"] == lab.space) & (clean["metric"] == "co2") & mask_period & clean["fault"].eq("")]
        if frame.empty:
            continue
        series = frame.set_index("time")["value"]
        local_days = sorted(set((series.index.tz_convert(settings.timezone)).date))
        events = event_times(local_days, lab, schedule, settings.timezone)
        responses = response_times(series, events, schedule, settings.interval_minutes)
        rises = day_rises(series, local_days, lab, schedule, settings.timezone, settings.interval_minutes)
        mismatched = mismatched_days(rises, schedule)
        no_people = set(mismatched.loc[mismatched["reason"] == "no_people", "day"])
        matched_responses = responses[~responses["day"].isin(no_people)]

        def by_event(table):
            rows = []
            for (clock_label, kind), group in table.assign(clock=table["time"].dt.tz_convert(settings.timezone)
                                                           .dt.strftime("%H:%M")).groupby(["clock", "label"], sort=True):
                minutes = group["response_min"].dropna()
                rows.append({
                    "time": clock_label, "label": kind, "kind": group["kind"].iloc[0], "events": int(len(group)),
                    "measured": int(group["baseline"].notna().sum()), "responded": int(len(minutes)),
                    "median_min": float(minutes.median()) if len(minutes) else None,
                    "p25_min": float(minutes.quantile(0.25)) if len(minutes) else None,
                    "p75_min": float(minutes.quantile(0.75)) if len(minutes) else None,
                    "median_change": round(float(group["change"].median()), 1) if group["change"].notna().any() else None,
                    "median_change_90": round(float(group["change_90"].median()), 1) if group["change_90"].notna().any() else None,
                })
            return rows

        judged = rises[rises["coverage"] >= schedule.min_coverage]
        month = pd.to_datetime(judged["day"]).dt.strftime("%Y-%m")
        by_month = []
        for name, group in judged.groupby(month, sort=True):
            class_days = group[group["class_day"]]
            by_month.append({
                "month": name, "class_days": int(len(class_days)),
                "no_people": int((class_days["rise"] < schedule.min_rise_ppm).sum()),
                "off_days": int((~group["class_day"]).sum()),
                "unscheduled_use": int((~group["class_day"] & (group["rise"] >= schedule.min_rise_ppm)).sum()),
                "median_rise_class": round(float(class_days["rise"].median()), 1) if len(class_days) else None,
            })
        out["spaces"][lab.space] = {
            "class_days": int(rises["class_day"].sum()), "judged_days": int(len(judged)),
            "response_all": by_event(responses),
            "response_matched": by_event(matched_responses),
            "mismatched": [{"day": str(r.day), "weekday": int(r.weekday), "reason": r.reason, "rise": round(float(r.rise), 1)}
                           for r in mismatched.itertuples()],
            "by_month": by_month,
        }
        out["spaces"][lab.space]["_frames"] = (responses, matched_responses, rises)
    return out


def setup_font():
    """한글 글꼴이 있으면 쓰고 True. 없으면 False (그래프 글자를 영문 slug로 바꾼다)."""
    import matplotlib
    from matplotlib import font_manager

    matplotlib.use("Agg")
    logging.getLogger("matplotlib.font_manager").setLevel(logging.ERROR)
    names = {f.name for f in font_manager.fontManager.ttflist}
    for name in KOREAN_FONTS:
        if name in names:
            matplotlib.rcParams["font.family"] = name
            matplotlib.rcParams["axes.unicode_minus"] = False
            return True
    return False


def style_axes(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def plot_profiles(profiles, settings, schedule, korean, out_dir):
    import matplotlib.pyplot as plt

    paths = []
    for metric in settings.analysis_metrics:
        items = [(space, value) for (space, m), value in profiles.items() if m == metric]
        if not items:
            continue
        fig, axes = plt.subplots(1, len(items), figsize=(4.2 * len(items), 3.4), sharey=True, squeeze=False)
        fig.patch.set_facecolor(SURFACE)
        for ax, (space, (profile, has_schedule)) in zip(axes[0], items):
            style_axes(ax)
            names = ({"on": "일정 있는 날", "off": "주말·공휴일"} if has_schedule else {"on": "평일", "off": "주말"}) \
                if korean else ({"on": "class days", "off": "weekend/holiday"} if has_schedule else {"on": "weekday", "off": "weekend"})
            for color, kind in zip(SERIES, ("on", "off")):
                part = profile[profile["kind"] == kind]
                if part.empty:
                    continue
                ax.fill_between(part["clock"], part["mean"] - part["std"], part["mean"] + part["std"], color=color,
                                alpha=0.12, linewidth=0)
                ax.plot(part["clock"], part["mean"], color=color, linewidth=2, label=names[kind])
            lab = schedule.for_space(space)
            if lab is not None:
                for event in lab.events:
                    ax.axvline(event.time.hour + event.time.minute / 60, color=TEXT_SECONDARY, linewidth=0.8,
                               linestyle=(0, (3, 3)))
            ax.set_xlim(0, 24)
            ax.set_xticks(range(0, 25, 6))
            ax.set_title(space if korean else settings.slug(space), color=TEXT, fontsize=11, loc="left")
            ax.set_xlabel("시각 (KST)" if korean else "hour (KST)", color=TEXT_SECONDARY, fontsize=9)
        axes[0][0].set_ylabel(METRIC_LABELS.get(metric, metric) if korean else metric, color=TEXT_SECONDARY, fontsize=9)
        axes[0][0].legend(frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY, loc="upper left")
        fig.suptitle(("하루 패턴: 평균 ± 표준편차 (학습+검증 기간, 점선 = 실습실 일정)" if korean else
                      f"daily profile: mean ± std (train+val), {metric}"), color=TEXT, fontsize=11, x=0.01, ha="left")
        fig.tight_layout()
        path = out_dir / f"002-daily-{metric}.png"
        fig.savefig(path, dpi=130, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)
    return paths


def plot_schedule(schedule_out, settings, schedule, korean, out_dir):
    """일정 변화별 반응 시간·변화 폭, 날마다 상승 폭."""
    import matplotlib.pyplot as plt

    paths = []
    for space, data in schedule_out["spaces"].items():
        responses, matched, rises = data["_frames"]
        slug = settings.slug(space)
        frame = matched.assign(clock=matched["time"].dt.tz_convert(settings.timezone).dt.strftime("%H:%M"))
        clocks = sorted(set(frame["clock"]))
        fig, axes = plt.subplots(1, 2, figsize=(9, 3.4))
        fig.patch.set_facecolor(SURFACE)
        for ax, column, ylabel in ((axes[0], "response_min", "반응 시간 (분)" if korean else "response (min)"),
                                   (axes[1], "change_90", "90분 안 변화 폭 (ppm)" if korean else "change within 90 min (ppm)")):
            style_axes(ax)
            groups = [frame.loc[frame["clock"] == c, column].dropna().to_numpy() for c in clocks]
            box = ax.boxplot(groups, widths=0.5, patch_artist=True, showfliers=True,
                             medianprops={"color": TEXT, "linewidth": 1.5},
                             flierprops={"marker": "o", "markersize": 3, "markerfacecolor": TEXT_SECONDARY,
                                         "markeredgewidth": 0})
            for patch in box["boxes"]:
                patch.set(facecolor=SERIES[0], alpha=0.35, edgecolor=SERIES[0])
            ax.set_xticks(range(1, len(clocks) + 1), [f"{c}\nn={len(g)}" for c, g in zip(clocks, groups)])
            ax.set_ylim(bottom=0)
            ax.set_ylabel(ylabel, color=TEXT_SECONDARY, fontsize=9)
        fig.suptitle((f"{space} 일정 변화 뒤 CO₂ (일정과 맞은 날, 학습+검증 기간)" if korean else
                      f"{slug}: CO₂ after schedule changes (matched days, train+val)"),
                     color=TEXT, fontsize=11, x=0.01, ha="left")
        fig.tight_layout()
        path = out_dir / "002-response.png"
        fig.savefig(path, dpi=130, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)

        fig, ax = plt.subplots(figsize=(10, 3.2))
        fig.patch.set_facecolor(SURFACE)
        style_axes(ax)
        days = pd.to_datetime(rises["day"])
        for color, flag, label in ((SERIES[0], True, "일정 있는 날" if korean else "class day"),
                                   (SERIES[1], False, "주말·공휴일" if korean else "weekend/holiday")):
            part = rises["class_day"] == flag
            ax.scatter(days[part], rises.loc[part, "rise"], s=12, color=color, label=label, linewidths=0)
        ax.axhline(schedule.min_rise_ppm, color=TEXT_SECONDARY, linewidth=0.8, linestyle=(0, (3, 3)))
        ax.set_ylabel("하루 상승 폭 (ppm)" if korean else "daily rise (ppm)", color=TEXT_SECONDARY, fontsize=9)
        ax.legend(frameon=False, fontsize=9, labelcolor=TEXT_SECONDARY, loc="upper left")
        ax.set_title((f"{space} 날마다 CO₂ 상승 폭 (활동 시간 최댓값 - 아침 기준, 점선 {schedule.min_rise_ppm:g}ppm)" if korean
                      else f"{slug}: daily CO₂ rise"), color=TEXT, fontsize=11, loc="left")
        fig.tight_layout()
        path = out_dir / "002-daily-rise.png"
        fig.savefig(path, dpi=130, facecolor=SURFACE)
        plt.close(fig)
        paths.append(path)
    return paths


def diagnose(clean, stats, split, settings, schedule):
    periods = assign_period(clean["time"], split)
    analysis = periods.isin(["train", "val"])
    profiles = daily_profile(clean, settings, schedule, analysis)
    result = {
        "analysis_period": {"start": split["train"]["start"].isoformat(), "end": split["val"]["end"].isoformat(),
                            "note": "패턴·일정 분석은 학습+검증 기간만. 품질(결측·고장)은 전체 기간"},
        "quality": quality(clean, stats, split, settings),
        "night_drift": night_drift(clean, settings),
        "profile_facts": profile_facts(profiles),
        "schedule": schedule_analysis(clean, settings, schedule, analysis),
    }
    return result, profiles


def run(settings, schedule):
    clean = load_clean(clean_path(settings))
    stats = pd.read_csv(settings.processed_dir / "clean_stats.csv", dtype={"space": str, "metric": str})
    if not split_path(settings).exists():
        print("artifacts/split.json이 없다. 먼저 split을 실행한다")
        return 1
    split = load_split(split_path(settings))
    result, profiles = diagnose(clean, stats, split, settings, schedule)

    korean = setup_font()
    out_dir = settings.reports_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)
    figures = plot_profiles(profiles, settings, schedule, korean, out_dir)
    figures += plot_schedule(result["schedule"], settings, schedule, korean, out_dir)
    for data in result["schedule"]["spaces"].values():
        data.pop("_frames")
    result["figures"] = [str(p.relative_to(settings.reports_dir)) for p in figures]

    settings.processed_dir.mkdir(parents=True, exist_ok=True)
    path = settings.processed_dir / "diagnosis.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"진단 → {path}")
    print(f"그래프 {len(figures)}개 → {out_dir}{'' if korean else ' (한글 글꼴이 없어 영문 표기)'}")
    for space, data in result["schedule"]["spaces"].items():
        print(f"\n{space} 일정 반응 (일정과 맞은 날, 학습+검증):")
        for row in data["response_matched"]:
            median = f"{row['median_min']:.0f}분" if row["median_min"] is not None else "반응 없음"
            print(f"  {row['time']} {row['label']}: 중앙값 {median}, 반응 {row['responded']}/{row['measured']}회, "
                  f"변화 폭 중앙값 {row['median_change']} ppm")
        reasons = pd.Series([m["reason"] for m in data["mismatched"]]).value_counts().to_dict()
        print(f"  어긋난 날: 사람 없던 일정일 {reasons.get('no_people', 0)}일, 일정 없이 쓴 날 {reasons.get('unscheduled_use', 0)}일 "
              f"(판정 {data['judged_days']}일)")
    for space, drift in result["night_drift"].items():
        print(f"밤 CO₂ 바닥값 {space}: 중앙값 {drift['median_floor']} ppm, 의심 {drift['flagged']}/{drift['nights']}밤")
    return 0
