from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    def load(name):
        return (FIXTURES / name).read_bytes()
    return load


@pytest.fixture
def root():
    return ROOT
