"""Tests for todo_create.py — occurrence generation and type helpers."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from notion_automations.todo_create import generate_occurrences, suggested_todo_type

TZ = ZoneInfo("Europe/Berlin")


def _class_row(
    start: str,
    end: str | None,
    weeks: str,
    day: str,
    start_decimal: float | None = 10.0,
) -> dict:  # type: ignore[type-arg]
    return {
        "properties": {
            "Dates": {"date": {"start": start, "end": end}},
            "Weeks": {"select": {"name": weeks}},
            "Day": {"select": {"name": day}},
            "Start Time (Decimal)": {"number": start_decimal},
        }
    }


class TestGenerateOccurrences:
    def test_single(self) -> None:
        row = _class_row("2026-04-22", None, "Single", "Wednesday")
        kept, skipped = generate_occurrences(row, TZ)
        assert len(kept) == 1
        assert kept[0] == datetime(2026, 4, 22, 10, 0, tzinfo=TZ)
        assert skipped == []

    def test_all_weekly(self) -> None:
        # 3 Tuesdays: Apr 21, Apr 28, May 5
        row = _class_row("2026-04-21", "2026-05-05", "All", "Tuesday")
        kept, skipped = generate_occurrences(row, TZ)
        assert len(kept) == 3
        assert kept[0] == datetime(2026, 4, 21, 10, 0, tzinfo=TZ)
        assert kept[1] == datetime(2026, 4, 28, 10, 0, tzinfo=TZ)
        assert kept[2] == datetime(2026, 5, 5, 10, 0, tzinfo=TZ)
        assert skipped == []

    def test_odd_biweekly(self) -> None:
        # Every 2 weeks on Monday: Apr 20, May 4, May 18
        row = _class_row("2026-04-20", "2026-05-18", "Odd", "Monday")
        kept, _skipped = generate_occurrences(row, TZ)
        assert len(kept) == 3
        assert kept[0] == datetime(2026, 4, 20, 10, 0, tzinfo=TZ)
        assert kept[1] == datetime(2026, 5, 4, 10, 0, tzinfo=TZ)
        assert kept[2] == datetime(2026, 5, 18, 10, 0, tzinfo=TZ)

    def test_snaps_to_correct_weekday(self) -> None:
        # start_str is a Monday but day=Tuesday — should snap forward
        row = _class_row("2026-04-20", "2026-04-28", "All", "Tuesday")
        kept, _ = generate_occurrences(row, TZ)
        assert kept[0].weekday() == 1  # Tuesday

    def test_stops_at_semester_end(self) -> None:
        # end_date is mid-week — last occurrence must not exceed it
        row = _class_row("2026-04-21", "2026-04-27", "All", "Tuesday")
        kept, _ = generate_occurrences(row, TZ)
        assert len(kept) == 1
        assert kept[0].date().isoformat() == "2026-04-21"

    def test_all_weekly_no_end_uses_fallback(self) -> None:
        # Dates.end absent but Weeks=All — should use fallback_end
        row = _class_row("2026-04-22", None, "All", "Wednesday")
        kept, _ = generate_occurrences(row, TZ, fallback_end="2026-05-06")
        assert len(kept) == 3
        assert kept[0].date().isoformat() == "2026-04-22"
        assert kept[1].date().isoformat() == "2026-04-29"
        assert kept[2].date().isoformat() == "2026-05-06"

    def test_all_weekly_no_end_no_fallback_returns_single(self) -> None:
        # No end and no fallback — treated as single occurrence
        row = _class_row("2026-04-22", None, "All", "Wednesday")
        kept, _ = generate_occurrences(row, TZ)
        assert len(kept) == 1

    def test_no_start_returns_empty(self) -> None:
        row = {"properties": {"Dates": {"date": {"start": None, "end": None}}}}
        kept, skipped = generate_occurrences(row, TZ)
        assert kept == []
        assert skipped == []

    def test_decimal_time_encoding(self) -> None:
        # 8.5 → 08:30, 10.25 → 10:15
        row = _class_row("2026-04-21", None, "Single", "Tuesday", start_decimal=8.5)
        kept, _ = generate_occurrences(row, TZ)
        assert kept[0].hour == 8
        assert kept[0].minute == 30

        row2 = _class_row("2026-04-21", None, "Single", "Tuesday", start_decimal=10.25)
        kept2, _ = generate_occurrences(row2, TZ)
        assert kept2[0].hour == 10
        assert kept2[0].minute == 15

    def test_stops_before_exam_week(self) -> None:
        # Exam week starts May 4 (Mon); last Tuesday before that is Apr 28
        row = _class_row("2026-04-21", "2026-05-19", "All", "Tuesday")
        kept, _ = generate_occurrences(row, TZ, exam_week_start="2026-05-04")
        assert all(occ.date() < date(2026, 5, 4) for occ in kept)
        assert kept[-1].date().isoformat() == "2026-04-28"

    def test_exam_week_start_itself_excluded(self) -> None:
        # Exam week starts on a Tuesday — that Tuesday should not appear
        row = _class_row("2026-04-21", "2026-05-19", "All", "Tuesday")
        kept, _ = generate_occurrences(row, TZ, exam_week_start="2026-04-28")
        assert all(occ.date().isoformat() != "2026-04-28" for occ in kept)

    def test_skip_ranges_move_to_skipped(self) -> None:
        # Apr 28 falls in a recess week Apr 27-May 3; should appear in skipped
        row = _class_row("2026-04-21", "2026-05-12", "All", "Tuesday")
        skip = [(date(2026, 4, 27), date(2026, 5, 3))]
        kept, skipped = generate_occurrences(row, TZ, skip_ranges=skip)
        kept_dates = [o.date().isoformat() for o in kept]
        skipped_dates = [o.date().isoformat() for o in skipped]
        assert "2026-04-28" not in kept_dates
        assert "2026-04-28" in skipped_dates
        assert "2026-04-21" in kept_dates
        assert "2026-05-05" in kept_dates

    def test_exam_and_skip_combined(self) -> None:
        # Exam week May 4; recess Apr 27-May 3; only Apr 21 kept
        row = _class_row("2026-04-21", "2026-05-19", "All", "Tuesday")
        skip = [(date(2026, 4, 27), date(2026, 5, 3))]
        kept, skipped = generate_occurrences(
            row, TZ, exam_week_start="2026-05-04", skip_ranges=skip
        )
        assert [o.date().isoformat() for o in kept] == ["2026-04-21"]
        assert [o.date().isoformat() for o in skipped] == ["2026-04-28"]


class TestSuggestedTodoType:
    @pytest.mark.parametrize(
        "class_type,expected",
        [
            ("Lecture", "Lecture"),
            ("Exercise", "Tutorial"),
            ("Seminar", "Reflection"),
            ("Unknown", "Tutorial"),
            ("", "Tutorial"),
        ],
    )
    def test_mapping(self, class_type: str, expected: str) -> None:
        assert suggested_todo_type(class_type) == expected
