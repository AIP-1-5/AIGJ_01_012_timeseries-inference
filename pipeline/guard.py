"""보드(Jetson)에서 학습을 막는다 (2026-10-22 팀 결정).

보드 B가 학습 부하로 두 번 멈췄다(10-21 18:55, 10-22 09:10). 학습은 맥이 켜져 있을 때만 하고,
보드는 변환·측정(과제 6·7)과 운영(과제 1·8)만 한다. 꼭 필요하면 ALLOW_BOARD_TRAINING=1로 우회한다.
"""

import os
import platform
import sys
from pathlib import Path


def on_jetson():
    return Path("/etc/nv_tegra_release").exists() or "tegra" in platform.release()


def refuse_training_on_board(what):
    """보드에서 학습하려 하면 이유를 출력하고 True. 호출한 쪽은 종료 코드 2로 끝낸다."""
    if not on_jetson() or os.environ.get("ALLOW_BOARD_TRAINING") == "1":
        return False
    print(f"거부: 보드에서는 학습({what})을 하지 않는다. 맥에서 실행하고 모델만 보드로 옮긴다 "
          "(CLAUDE.md '하지 말 것'). 꼭 필요하면 ALLOW_BOARD_TRAINING=1", file=sys.stderr)
    return True


def refuse_heavy_on_board(what):
    """1년치 데이터를 메모리에 올리는 준비 작업도 보드에서는 하지 않는다."""
    if not on_jetson() or os.environ.get("ALLOW_BOARD_TRAINING") == "1":
        return False
    print(f"거부: 보드에서는 무거운 준비({what})를 하지 않는다. 맥에서 실행한다 (CLAUDE.md '하지 말 것')", file=sys.stderr)
    return True
