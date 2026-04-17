"""ICS export logic for Notion Classes DB."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ics import Calendar, Event  # type: ignore[import-untyped]
from ics.grammar.parse import ContentLine  # type: ignore[import-untyped]

_DAY_MAP = {
    "Monday": "MO",
    "Tuesday": "TU",
    "Wednesday": "WE",
    "Thursday": "TH",
    "Friday": "FR",
    "Saturday": "SA",
    "Sunday": "SU",
}

# Reverse map: BYDAY code → Python weekday() integer (Mon=0)
_BYDAY_TO_WEEKDAY = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}

TUM_LOCATION = "Technical University of Munich, Arcisstraße 21, 80333 Munich, Germany"


def _combine_date_time(
    date_str: str, decimal_time: float | None, tz: ZoneInfo
) -> datetime:
    """Build a timezone-aware datetime from a date string and optional decimal hour.

    Handles two Notion date formats:
    - Full datetime string (e.g. "2024-04-12T10:00:00+00:00") — used directly.
    - Date-only string (e.g. "2024-04-12") — combined with decimal_time if given,
      otherwise midnight in the given timezone.

    decimal_time: fractional hour (e.g. 8.5 = 08:30, 10.25 = 10:15).
    """
    if "T" in date_str:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        return dt if dt.tzinfo is not None else dt.replace(tzinfo=tz)
    parsed = date.fromisoformat(date_str)
    if decimal_time is not None:
        h = int(decimal_time)
        m = round((decimal_time - h) * 60)
        return datetime(parsed.year, parsed.month, parsed.day, h, m, tzinfo=tz)
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=tz)


def _snap_to_weekday(date_str: str, byday: str) -> str:
    """Advance date_str to the first occurrence of byday if it doesn't already match.

    Prevents DTSTART/BYDAY mismatches that cause calendar apps to insert a
    spurious extra occurrence on the original (wrong) weekday.
    """
    d = date.fromisoformat(date_str)
    target = _BYDAY_TO_WEEKDAY[byday]
    days_ahead = (target - d.weekday()) % 7
    if days_ahead:
        d = d + timedelta(days=days_ahead)
    return d.isoformat()


def classes_to_ics(
    classes: list[dict[str, Any]],
    mapping: dict[str, str],
    ics_path: str,
    timezone: str = "Europe/Berlin",
) -> None:
    """Convert a list of Notion Classes DB rows to an iCalendar file.

    mapping keys:
        name     — title property (e.g. "Title")
        start    — date-range property (e.g. "Dates"); .start = first occurrence,
                   .end = semester end date used as RRULE UNTIL
        location — (optional) select or rich_text property used as event notes

    "Start Time (Decimal)" and "End Time (Decimal)" number properties are read
    directly from the row and used to set the event start/end times within the day.

    The iCalendar LOCATION field is always set to the TU Munich address so events
    pin to the campus on a map. The room/venue goes into the event description.
    """
    tz = ZoneInfo(timezone)
    cal = Calendar()
    skipped = 0

    # Derive a fallback semester-end from the latest Dates.end across all rows.
    # Used for recurring events that have no explicit end date.
    fallback_semester_end: str | None = (
        max(
            (
                row["properties"].get(mapping["start"], {}).get("date", {}).get("end")
                or ""
                for row in classes
            ),
            default="",
        )
        or None
    )

    for idx, row in enumerate(classes):
        try:
            props = row["properties"]
            event = Event()

            # Name
            name_val = props.get(mapping["name"], {})
            event.name = (
                name_val.get("title", [{}])[0].get("plain_text")
                if name_val.get("title")
                else name_val.get("name", "Class")
            ) or "Class"

            # Recurrence metadata (needed before setting DTSTART to allow snapping)
            weeks_val: str | None = props.get("Weeks", {}).get("select", {}).get("name")
            day_val: str | None = props.get("Day", {}).get("select", {}).get("name")
            byday = _DAY_MAP.get(day_val or "")
            is_recurring = weeks_val and weeks_val != "Single" and byday

            # Dates: single date-range property.
            #   .start = date of first (or only) occurrence
            #   .end   = semester end date for recurring events; None for single events
            dates_obj = props.get(mapping["start"], {}).get("date", {})
            start_date_str: str | None = dates_obj.get("start")
            semester_end_str: str | None = dates_obj.get("end")

            start_decimal: float | None = props.get("Start Time (Decimal)", {}).get(
                "number"
            )
            end_decimal: float | None = props.get("End Time (Decimal)", {}).get(
                "number"
            )

            if not start_date_str:
                raise ValueError("No start date")

            # For recurring events, snap DTSTART to the declared weekday so that
            # calendar apps don't insert a spurious extra occurrence on the original
            # (potentially wrong) date.
            if is_recurring and byday:
                start_date_str = _snap_to_weekday(start_date_str, byday)

            event.begin = _combine_date_time(start_date_str, start_decimal, tz)
            # DTEND = same date as first occurrence + session end time
            if end_decimal is not None:
                event.end = _combine_date_time(start_date_str, end_decimal, tz)

            # Venue → notes; campus address → LOCATION
            venue: str | None = None
            if "location" in mapping:
                loc_val = props.get(mapping["location"], {})
                venue = (
                    (
                        loc_val.get("rich_text")
                        and loc_val["rich_text"][0].get("plain_text")
                    )
                    or loc_val.get("select", {}).get("name")
                    or None
                )
            event.location = TUM_LOCATION
            if venue:
                event.description = f"Venue: {venue}"

            # Recurrence rule
            if is_recurring and byday:
                if weeks_val == "All":
                    rrule = f"FREQ=WEEKLY;BYDAY={byday}"
                else:
                    # Odd/Even: every other week; DTSTART anchors which weeks are hit.
                    rrule = f"FREQ=WEEKLY;INTERVAL=2;BYDAY={byday};WKST=MO"
                # Add UNTIL (UTC per RFC 5545); fall back to latest end across rows.
                until_source = semester_end_str or fallback_semester_end
                if until_source:
                    until_dt = _combine_date_time(
                        until_source, end_decimal, tz
                    ).astimezone(UTC)
                    rrule += f";UNTIL={until_dt.strftime('%Y%m%dT%H%M%SZ')}"
                event.extra.append(ContentLine("RRULE", {}, rrule))

            cal.events.add(event)
        except Exception as e:
            print(f"[WARN] Skipping row {idx}: {e}")
            skipped += 1

    with open(ics_path, "w") as f:
        for chunk in cal.serialize_iter():
            f.write(chunk)
    print(f"Exported {len(cal.events)} events to {ics_path} (skipped {skipped}).")
