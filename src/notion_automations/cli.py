import os
import subprocess
import sys
import tempfile
import time
from typing import Any

import questionary
import typer
from questionary import Style

from notion_automations.ics_export import classes_to_ics
from notion_automations.notion import (
    fetch_classes_db,
    fetch_courses_db,
    fetch_semesters_db,
)

app = typer.Typer()

# Maps logical field names to actual Notion property names in the Classes DB.
DEFAULT_MAPPING: dict[str, str] = {
    "name": "Title",
    "start": "Dates",  # date-range: .start = first occurrence, .end = semester end
    "location": "Venue",
}
DEFAULT_TIMEZONE = "Europe/Berlin"

_STYLE = Style(
    [
        ("qmark", "fg:#00b4d8 bold"),
        ("question", "bold"),
        ("answer", "fg:#0077b6 bold"),
        ("pointer", "fg:#00b4d8 bold"),
        ("highlighted", "fg:#00b4d8 bold"),
        ("selected", "fg:#0096c7"),
        ("instruction", "fg:#888888 italic"),
        ("text", ""),
        ("separator", "fg:#444444"),
    ]
)


def get_unique_values(
    rows: list[Any], prop: str, subkey: str | None = None
) -> list[str]:
    values: set[str] = set()
    for row in rows:
        val = row["properties"].get(prop)
        if not val:
            continue
        if subkey:
            pass
        else:
            if val.get("select") and val["select"].get("name"):
                values.add(val["select"]["name"])
            elif val.get("title") and val["title"]:
                values.add(val["title"][0]["plain_text"])
    return sorted(values)


def _interactive_filter(classes: list[Any]) -> list[Any]:
    """Filter classes interactively by semester or course via Notion relations."""
    filter_by = questionary.select(
        "Filter classes by:",
        choices=["All classes", "Semester", "Course"],
        style=_STYLE,
    ).ask()

    if not filter_by or filter_by == "All classes":
        return classes

    selected: list[str] | None

    if filter_by == "Semester":
        sem_rows = fetch_semesters_db()
        label_to_id: dict[str, str] = {}
        for row in sem_rows:
            props = row["properties"]
            name_parts = props.get("Semester", {}).get("title", [])
            name = name_parts[0]["plain_text"] if name_parts else "?"
            uni = props.get("University", {}).get("select", {}).get("name", "")
            label = f"{name}  ({uni})" if uni else name
            label_to_id[label] = row["id"]
        if not label_to_id:
            typer.echo("No semesters found — exporting all.")
            return classes
        selected = questionary.checkbox(
            "Select semesters (Space to toggle, Enter to confirm):",
            choices=sorted(label_to_id),
            style=_STYLE,
        ).ask()
        if not selected:
            return classes
        selected_sem_ids = {label_to_id[lbl] for lbl in selected}
        course_ids: set[str] = set()
        for row in sem_rows:
            if row["id"] in selected_sem_ids:
                for c in row["properties"].get("Courses", {}).get("relation", []):
                    course_ids.add(c["id"])
        return [
            r
            for r in classes
            if any(
                c["id"] in course_ids
                for c in r["properties"].get("Course", {}).get("relation", [])
            )
        ]

    # filter_by == "Course"
    course_rows = fetch_courses_db()
    label_to_course_id: dict[str, str] = {}
    for row in course_rows:
        props = row["properties"]
        name_parts = props.get("Name", {}).get("title", [])
        name = name_parts[0]["plain_text"] if name_parts else "?"
        code = props.get("Code", {}).get("select", {}).get("name", "")
        label = f"{code}  —  {name}" if code else name
        label_to_course_id[label] = row["id"]
    if not label_to_course_id:
        typer.echo("No courses found — exporting all.")
        return classes
    selected = questionary.checkbox(
        "Select courses (Space to toggle, Enter to confirm):",
        choices=sorted(label_to_course_id),
        style=_STYLE,
    ).ask()
    if not selected:
        return classes
    selected_course_ids = {label_to_course_id[lbl] for lbl in selected}
    return [
        r
        for r in classes
        if any(
            c["id"] in selected_course_ids
            for c in r["properties"].get("Course", {}).get("relation", [])
        )
    ]


@app.command()
def export_classes_ics(
    db_id: str = typer.Option("", help="Notion DB ID (default: env var)"),
    ics_path: str = typer.Option("", help="Output .ics path (temp file when --open)"),
    timezone: str = typer.Option(DEFAULT_TIMEZONE, help="Timezone for class times"),
    open_calendar: bool = typer.Option(
        False, "--open", help="Open in Calendar and clean up after import"
    ),
) -> None:
    """Export classes from Notion DB to an .ics file with interactive filter."""
    if not db_id:
        db_id = os.environ.get("NOTION_CLASSES_DB_ID", "")
    if not db_id:
        typer.echo("Database ID must be provided via --db-id or NOTION_CLASSES_DB_ID.")
        sys.exit(1)

    classes = fetch_classes_db(db_id)
    if not classes:
        typer.echo("No classes found.")
        sys.exit(1)

    if sys.stdin.isatty():
        classes = _interactive_filter(classes)
        if not classes:
            typer.echo("No classes matched the selection.")
            sys.exit(0)

    tmp_fd: int | None = None
    out_path = ics_path
    if open_calendar and not ics_path:
        tmp_fd, out_path = tempfile.mkstemp(suffix=".ics", prefix="notion_classes_")
        os.close(tmp_fd)
    elif not ics_path:
        out_path = "classes.ics"

    classes_to_ics(classes, DEFAULT_MAPPING, out_path, timezone)

    if open_calendar:
        subprocess.run(["open", out_path], check=True)
        delay = 30
        typer.echo(f"Opened in Calendar — cleaning up in {delay}s (Ctrl+C to cancel).")
        try:
            time.sleep(delay)
        finally:
            if os.path.exists(out_path) and (tmp_fd is not None or not ics_path):
                os.remove(out_path)
                typer.echo("Cleaned up.")
