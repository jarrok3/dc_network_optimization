"""Calendar labels shared by fitting and prediction; never use generator multipliers."""
from datetime import date, timedelta

import pandas as pd

# Matches the current synthetic history, not an official Korean holiday calendar.
SYNTHETIC_HOLIDAYS = {"2026-08-17", "2026-09-24", "2026-09-25", "2026-10-03", "2026-10-09"}


def load_holidays(path=None) -> set[str]:
    """Optional CSV column 'date' replaces the synthetic fixture for all stages."""
    if path is None:
        return set(SYNTHETIC_HOLIDAYS)
    rows = pd.read_csv(path, dtype=str)
    if "date" not in rows or rows.date.duplicated().any():
        raise ValueError("holidays.csv requires a unique date column")
    return {date.fromisoformat(d).isoformat() for d in rows.date}


def holiday_class(day: date, holidays: set[str]) -> int:
    if day.isoformat() in holidays:
        return 2
    return 1 if (day + timedelta(days=1)).isoformat() in holidays else 0


def season(day: date) -> str:
    return ("winter", "spring", "summer", "autumn")[(day.month % 12) // 3]
