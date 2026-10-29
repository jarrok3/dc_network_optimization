"""Paths and independent random streams for the nationwide preprocessing pipeline."""
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
CACHE_DIR = ROOT / "code" / "cache"
DEMAND_DIR = ROOT / "code" / "demand"
STREAMS = ("data", "history", "distances", "planning", "search", "control")


def seed_sequence(name: str, seed: int = 437) -> np.random.SeedSequence:
    return np.random.SeedSequence(seed).spawn(len(STREAMS))[STREAMS.index(name)]
