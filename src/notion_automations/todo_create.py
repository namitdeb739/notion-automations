"""Occurrence generation and type helpers for the create-todos command."""

from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

_DAY_MAP: dict[str, int] = {
    "Monday": 0,
    "Tuesday": 1,
    "Wednesday": 2,
    "Thursday": 3,
    "Friday": 4,
    "Saturday": 5,
    "Sunday": 6,
}

# Maps Class.Type → default Course To-Do Type
_CLASS_TYPE_DEFAULTS: dict[str, str] = {
    "Lecture": "Lecture",
    "Exercise": "Tutorial",
    "Seminar": "Reflection",
}

TODO_TYPES: list[str] = [
    "Admin",
    "Lecture",
    "Tutorial",
    "Lab",
    "Reflection",
    "Assignment",
    "Problem Set",
    "Group Project",
    "Quiz",
    "Examination",
    "Extra Material",
]

PRIORITIES: list[str] = ["High", "Medium", "Low"]


def suggested_todo_type(class_type: str) -> str:
    return _CLASS_TYPE_DEFAULTS.get(class_type, "Tutorial")


def generate_occurrences(
    class_row: dict[str, Any],
    tz: ZoneInfo,
    fallback_end: str | None = None,
    exam_week_start: str | None = None,
    skip_ranges: list[tuple[date, date]] | None = None,
) -> tuple[list[datetime], list[datetime]]:
    """Return (kept, skipped) datetimes for each class session within the semester.

    - Stops before exam_week_start (exclusive) when provided.
    - Occurrences whose date falls within any skip_range are moved to skipped.
    - fallback_end is used when Dates.end is absent but Weeks is not Single.
    """
    props = class_row["properties"]
    dates_obj = props.get("Dates", {}).get("date", {})
    start_str: str | None = dates_obj.get("start")
    end_str: str | None = dates_obj.get("end") or fallback_end
    weeks: str | None = props.get("Weeks", {}).get("select", {}).get("name")
    day_str: str | None = props.get("Day", {}).get("select", {}).get("name")
    start_decimal: float | None = props.get("Start Time (Decimal)", {}).get("number")

    if not start_str:
        return [], []

    def _to_dt(d: date) -> datetime:
        if start_decimal is not None:
            h = int(start_decimal)
            m = round((start_decimal - h) * 60)
            return datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
        return datetime(d.year, d.month, d.day, 0, 0, tzinfo=tz)

    def _is_skipped(d: date) -> bool:
        if not skip_ranges:
            return False
        return any(r_start <= d <= r_end for r_start, r_end in skip_ranges)

    start_date = date.fromisoformat(start_str[:10])
    exam_date: date | None = (
        date.fromisoformat(exam_week_start[:10]) if exam_week_start else None
    )

    if weeks == "Single" or not end_str or not day_str:
        dt = _to_dt(start_date)
        if _is_skipped(start_date):
            return [], [dt]
        return [dt], []

    end_date = date.fromisoformat(end_str[:10])
    target_weekday = _DAY_MAP.get(day_str)
    if target_weekday is None:
        dt = _to_dt(start_date)
        return [dt], []

    days_ahead = (target_weekday - start_date.weekday()) % 7
    current = start_date + timedelta(days=days_ahead)
    step = timedelta(days=7) if weeks == "All" else timedelta(days=14)

    kept: list[datetime] = []
    skipped: list[datetime] = []
    while current <= end_date:
        if exam_date is not None and current >= exam_date:
            break
        dt = _to_dt(current)
        if _is_skipped(current):
            skipped.append(dt)
        else:
            kept.append(dt)
        current += step

    return kept, skipped
