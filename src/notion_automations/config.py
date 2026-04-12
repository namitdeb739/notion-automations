"""Typed configuration using dataclasses.

Define your project's configuration here. Dataclasses provide type safety,
default values, and easy serialization without external dependencies.

Usage:
    from notion_automations.config import Config

    config = Config()
    config = Config(seed=42, data_dir="data/processed")
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    """Project configuration."""

    # Paths
    data_dir: Path | str = Path("data")
    output_dir: Path | str = Path("output")

    # Reproducibility
    seed: int = 42

    def __post_init__(self) -> None:
        self.data_dir = Path(self.data_dir)
        self.output_dir = Path(self.output_dir)
