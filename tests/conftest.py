"""Shared test fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """A temporary data directory that exists on disk."""
    d = tmp_path / "data"
    d.mkdir()
    return d
