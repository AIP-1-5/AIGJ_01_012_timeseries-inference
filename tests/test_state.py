"""과제 5: 정답 정의와 입력 규칙."""

from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.settings import load_schedule
from state.__main__ import OCC_FEATURES, VENT_FEATURES, choose_threshold
from state.features import CALENDAR_FEATURES, observation_labels, occupancy_labels, ventilation_labels

ROOT = Path(__file__).resolve().parent.parent
TZ = "Asia/Seoul"


def kst(text):
    return pd.Timestamp(text, tz=TZ).tz_convert("UTC")


def test_occupancy_input_has_no_schedule_or_clock():
    assert not set(OCC_FEATURES) & set(CALENDAR_FEATURES)
    assert set(CALENDAR_FEATURES) <= set(VENT_FEATURES)


def test_occupancy_labels_exclude_lunch_and_transitions():
    schedule = load_schedule(ROOT / "config" / "schedule.toml")
    index = pd.date_range(kst("2026-03-09 00:05"), kst("2026-03-10 00:00"), freq="5min")
    labels = occupancy_labels(index, "실습실", schedule, TZ)
    at = lambda hhmm: labels[kst(f"2026-03-09 {hhmm}")]              # 칸 끝 시각
    assert at("10:05") == 1 and at("15:00") == 1 and at("20:00") == 0 and at("03:00") == 0
    assert np.isnan(at("12:00"))                                       # 점심
    assert np.isnan(at("09:05")) and np.isnan(at("13:10"))             # 수업 시작 직후 15분
    assert labels.loc[kst("2026-03-09 18:30")] == 0 and np.isnan(labels.loc[kst("2026-03-09 18:10")])
    assert occupancy_labels(index, "사무실", schedule, TZ).isna().all()


def test_ventilation_labels_exclude_already_high():
    index = pd.date_range("2026-03-09T00:00Z", periods=12, freq="5min")
    co2 = pd.Series([800, 850, 900, 950, 990, 1010, 1100, 990, 900, 800, 700, 600], index=index, dtype=float)
    labels = ventilation_labels(co2)
    assert labels.iloc[0] == 1                       # 25분 뒤 1,010 → 양성
    assert np.isnan(labels.iloc[5]) and np.isnan(labels.iloc[6])      # 이미 1,000 이상 → 제외
    assert labels.iloc[8] == 0                       # 앞으로 안 넘음
    assert np.isnan(labels.iloc[-1])                 # 미래 값 부족


def test_observation_labels():
    index = pd.date_range(kst("2026-10-22 09:05"), periods=12, freq="5min")
    obs = pd.DataFrame({"space": ["실습실", "실습실"], "start_kst": ["2026-10-22 09:00", "2026-10-22 09:30"],
                        "end_kst": ["2026-10-22 09:30", "2026-10-22 10:00"], "people": [20, 0]})
    labels = observation_labels(index, "실습실", obs, TZ)
    assert (labels.iloc[:6] == 1).all() and (labels.iloc[6:] == 0).all()


def test_threshold_recall_rule():
    y = np.array([0] * 90 + [1] * 10)
    prob = np.r_[np.linspace(0, 0.5, 90), np.linspace(0.3, 0.9, 10)]
    threshold, _ = choose_threshold(prob, y, "recall")
    assert ((prob >= threshold)[y == 1]).mean() >= 0.85


def test_observe_scores_direct_observations(tmp_path):
    """직접 관찰 기록 + 그 시간 수집기 DB → 모델·규칙 채점표 (다시 학습하지 않음)."""
    import pickle
    import shutil

    from sklearn.ensemble import HistGradientBoostingClassifier

    from collector.store import Store
    from state.__main__ import main as state_main
    from state.features import SENSOR_FEATURES
    from tests.test_service import insert_rows

    root = tmp_path
    (root / "config").mkdir()
    for name in ("preprocess.toml", "schedule.toml"):
        shutil.copy(ROOT / "config" / name, root / "config" / name)
    (root / "docs" / "observations").mkdir(parents=True)
    (root / "docs" / "reports").mkdir(parents=True)
    (root / "docs" / "observations" / "occupancy.csv").write_text(
        "space,start_kst,end_kst,people,note\n"
        "실습실,2026-04-14 12:10,2026-04-14 12:50,0,점심\n"
        "실습실,2026-04-14 13:40,2026-04-14 14:30,17,수업\n", encoding="utf-8")
    db = root / "collector.db"
    Store(db).close()
    insert_rows(db, pd.Timestamp("2026-04-14T01:00Z"), pd.Timestamp("2026-04-14T06:00Z"))
    rng = np.random.default_rng(0)
    co2 = np.r_[rng.normal(450, 10, 200), rng.normal(850, 10, 200)]
    X = pd.DataFrame({f: rng.normal(0, 1, 400) for f in SENSOR_FEATURES})
    X["co2"] = co2
    model = HistGradientBoostingClassifier(max_iter=20).fit(X[SENSOR_FEATURES], (co2 > 650).astype(int))
    with (root / "models.pkl").open("wb") as fp:
        pickle.dump({"occupancy": model, "occupancy_threshold": 0.5, "occupancy_features": SENSOR_FEATURES}, fp)
    assert state_main(["observe", "--config-dir", str(root / "config"), "--db", str(db), "--models", str(root / "models.pkl")]) == 0
    text = (root / "docs" / "reports" / "005-observation.md").read_text(encoding="utf-8")
    assert "| 모델 |" in text and "비어있음→사용중" in text
    assert "| 실습실 | 04-14 12:15 | 0 |" in text and "| 실습실 | 04-14 14:00 | 1 |" in text


def test_occupancy_threshold_keeps_false_occupied_under_limit():
    from state.__main__ import FALSE_OCC_LIMIT

    rng = np.random.default_rng(0)
    y = np.r_[np.zeros(1000), np.ones(300)].astype(int)
    prob = np.r_[rng.uniform(0, 0.6, 1000), rng.uniform(0.3, 1.0, 300)]
    threshold, table = choose_threshold(prob, y, "control")
    false_occ = (prob[y == 0] >= threshold).mean()
    assert false_occ <= FALSE_OCC_LIMIT
    ok = table[table["false_occupied_rate"] <= FALSE_OCC_LIMIT]
    assert threshold == ok.sort_values(["recall", "f1", "threshold"], ascending=[False, False, False]).iloc[0]["threshold"]
    f1_threshold, _ = choose_threshold(prob, y, "f1")
    assert "false_occupied_rate" in table.columns and f1_threshold > 0
