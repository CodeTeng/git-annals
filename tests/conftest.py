"""共享 fixture。"""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import make_repo


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    return make_repo(tmp_path / "demo-repo")
