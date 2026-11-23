import sys
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402

from adapters.fakes import FakeClock  # noqa: E402


@pytest.fixture()
def clock() -> FakeClock:
    return FakeClock(datetime(2026, 11, 23, 10, 0, 0))
