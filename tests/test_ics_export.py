import os
import tempfile
from typing import Any

from notion_automations.ics_export import classes_to_ics


def test_classes_to_ics_single_event() -> None:
    """Single event with decimal times in UTC for deterministic assertions."""
    classes: list[dict[str, Any]] = [
        {
            "properties": {
                "Title": {"title": [{"plain_text": "Math"}]},
                "Dates": {"date": {"start": "2024-04-12", "end": None}},
                "Start Time (Decimal)": {"number": 10.0},
                "End Time (Decimal)": {"number": 11.5},
                "Venue": {"select": {"name": "Room 101"}},
            }
        }
    ]
    mapping: dict[str, str] = {"name": "Title", "start": "Dates", "location": "Venue"}
    with tempfile.TemporaryDirectory() as tmpdir:
        ics_path = os.path.join(tmpdir, "classes.ics")
        classes_to_ics(classes, mapping, ics_path, timezone="UTC")
        with open(ics_path) as f:
            content = f.read()
        assert "BEGIN:VCALENDAR" in content
        assert "SUMMARY:Math" in content
        assert "Technical University of Munich" in content
        assert "DESCRIPTION:Venue: Room 101" in content
        assert "DTSTART:20240412T100000Z" in content
        assert "DTEND:20240412T113000Z" in content


def test_classes_to_ics_recurring_event() -> None:
    """Recurring weekly event has RRULE with UNTIL from semester end."""
    classes: list[dict[str, Any]] = [
        {
            "properties": {
                "Title": {"title": [{"plain_text": "IN2049 Lecture"}]},
                "Dates": {"date": {"start": "2024-04-18", "end": "2024-07-18"}},
                "Start Time (Decimal)": {"number": 8.5},
                "End Time (Decimal)": {"number": 10.25},
                "Venue": {"select": {"name": "MW 1450"}},
                "Weeks": {"select": {"name": "All"}},
                "Day": {"select": {"name": "Thursday"}},
            }
        }
    ]
    mapping: dict[str, str] = {"name": "Title", "start": "Dates", "location": "Venue"}
    with tempfile.TemporaryDirectory() as tmpdir:
        ics_path = os.path.join(tmpdir, "classes.ics")
        classes_to_ics(classes, mapping, ics_path, timezone="UTC")
        with open(ics_path) as f:
            content = f.read()
        assert "DTSTART:20240418T083000Z" in content
        assert "DTEND:20240418T101500Z" in content
        assert "RRULE:FREQ=WEEKLY;BYDAY=TH;UNTIL=20240718T101500Z" in content
