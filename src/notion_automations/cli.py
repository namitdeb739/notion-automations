import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import questionary
import typer
from questionary import Style

from notion_automations.gpa_project import GRADE_POINTS, PENDING_GRADES, project_gpa
from notion_automations.ics_export import classes_to_ics, exams_to_ics
from notion_automations.notion import (
    apply_template_to_page,
    create_course_todo,
    ensure_courses_github_property,
    fetch_classes_db,
    fetch_classes_ds,
    fetch_course_todos_for_course,
    fetch_course_todos_templates,
    fetch_courses_db,
    fetch_examinations_db,
    fetch_gpa_db,
    fetch_page,
    fetch_semesters_db,
    get_course_todos_db_id,
    update_course_github_url,
)
from notion_automations.todo_create import (
    PRIORITIES,
    TODO_TYPES,
    generate_occurrences,
    suggested_todo_type,
)

app = typer.Typer()


@app.command(name="help")
def help_cmd() -> None:
    """List all available na commands."""
    import click

    ctx = click.get_current_context()
    typer.echo((ctx.parent or ctx).get_help())


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
    timezone: str = typer.Option(
        "", help="Timezone (auto-detected: TUM→Europe/Berlin, else Asia/Singapore)"
    ),
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

    # Build per-course semester schedule data.
    fallback_end_by_course: dict[str, str] = {}
    exam_week_by_course: dict[str, str] = {}
    skip_ranges_by_course: dict[str, list[tuple[str, str]]] = {}
    universities: set[str] = set()
    unique_course_ids: set[str] = set()
    for row in classes:
        for c in row["properties"].get("Course", {}).get("relation", []):
            unique_course_ids.add(c["id"])
    for cid in unique_course_ids:
        course_page = fetch_page(cid)
        sem_rels = course_page["properties"].get("Semester", {}).get("relation", [])
        if not sem_rels:
            continue
        sem_page = fetch_page(sem_rels[0]["id"])
        sp = sem_page["properties"]
        uni = sp.get("University", {}).get("select", {}).get("name", "")
        if uni:
            universities.add(uni)
        sem_end = sp.get("Semester Dates", {}).get("date", {}).get("end")
        if sem_end:
            fallback_end_by_course[cid] = sem_end
        exam_start = sp.get("Examination Weeks", {}).get("date", {}).get("start")
        if exam_start:
            exam_week_by_course[cid] = exam_start
        skip_raw: list[tuple[str, str]] = []
        for key in ("Recess Week", "Reading Week"):
            dt = (sp.get(key) or {}).get("date") or {}
            if dt.get("start") and dt.get("end"):
                skip_raw.append((dt["start"], dt["end"]))
        if skip_raw:
            skip_ranges_by_course[cid] = skip_raw

    if not timezone:
        if "TUM" in universities:
            timezone = "Europe/Berlin"
        elif universities:
            timezone = "Asia/Singapore"
        else:
            timezone = DEFAULT_TIMEZONE

    classes_to_ics(
        classes,
        DEFAULT_MAPPING,
        out_path,
        timezone,
        fallback_end_by_course,
        exam_week_by_course,
        skip_ranges_by_course,
    )

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


@app.command()
def export_exams_ics(
    db_id: str = typer.Option("", help="Examinations DB ID (default: built-in)"),
    ics_path: str = typer.Option("", help="Output .ics path (temp file when --open)"),
    timezone: str = typer.Option(
        "", help="Timezone (auto-detected: TUM→Europe/Berlin, else Asia/Singapore)"
    ),
    open_calendar: bool = typer.Option(
        False, "--open", help="Open in Calendar and clean up after import"
    ),
) -> None:
    """Export Examinations from Notion to an .ics file with interactive filter."""
    exams = fetch_examinations_db(db_id or None)
    exams = [row for row in exams if row["properties"].get("Date", {}).get("date")]
    if not exams:
        typer.echo("No examinations with a date found.")
        sys.exit(0)

    if sys.stdin.isatty():
        exams = _interactive_filter(exams)
        if not exams:
            typer.echo("No examinations matched the selection.")
            sys.exit(0)

    if not timezone:
        exam_course_ids: set[str] = set()
        for row in exams:
            for c in row["properties"].get("Course", {}).get("relation", []):
                exam_course_ids.add(c["id"])
        exam_universities: set[str] = set()
        for cid in exam_course_ids:
            course_page = fetch_page(cid)
            sem_rels = course_page["properties"].get("Semester", {}).get("relation", [])
            if sem_rels:
                sem_page = fetch_page(sem_rels[0]["id"])
                uni = (
                    sem_page["properties"]
                    .get("University", {})
                    .get("select", {})
                    .get("name", "")
                )
                if uni:
                    exam_universities.add(uni)
        if "TUM" in exam_universities:
            timezone = "Europe/Berlin"
        elif exam_universities:
            timezone = "Asia/Singapore"
        else:
            timezone = DEFAULT_TIMEZONE

    tmp_fd: int | None = None
    out_path = ics_path
    if open_calendar and not ics_path:
        tmp_fd, out_path = tempfile.mkstemp(suffix=".ics", prefix="notion_exams_")
        os.close(tmp_fd)
    elif not ics_path:
        out_path = "exams.ics"

    exams_to_ics(exams, out_path, timezone)

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


@app.command()
def gpa_project(
    ds_id: str = typer.Option("", help="GPA DB data source ID (default: built-in)"),
) -> None:
    """Display current GPA and project with hypothetical grades for pending courses."""
    gpa_rows = fetch_gpa_db(ds_id or None)
    if not gpa_rows:
        typer.echo("No GPA rows found.")
        raise typer.Exit()

    grade_choices = [*GRADE_POINTS, "CS", "CU"]

    for row in gpa_rows:
        props = row["properties"]

        name_parts = props.get("Name", {}).get("title", [])
        name = name_parts[0]["plain_text"] if name_parts else "?"

        weighted_gp: float = (
            props.get("Weighted GP", {}).get("rollup", {}).get("number") or 0.0
        )
        counted_mcs: float = (
            props.get("Total Counted MCs", {}).get("rollup", {}).get("number") or 0.0
        )
        gpa_formula: float | None = (
            props.get("GPA", {}).get("formula", {}).get("number")
        )
        gpa_display = (
            f"{gpa_formula:.4f}"
            if gpa_formula is not None
            else (f"{weighted_gp / counted_mcs:.4f}" if counted_mcs else "—")
        )
        typer.echo(f"{name}  →  GPA: {gpa_display}  ({counted_mcs:.0f} counted MCs)")

        if not sys.stdin.isatty():
            continue

        course_ids = {c["id"] for c in props.get("Courses", {}).get("relation", [])}
        if not course_ids:
            continue

        all_courses = fetch_courses_db()
        pending = [
            c
            for c in all_courses
            if c["id"] in course_ids
            and c["properties"].get("Grade", {}).get("select", {}).get("name")
            in PENDING_GRADES
            and c["properties"].get("MCs", {}).get("number") is not None
        ]

        if not pending:
            continue

        typer.echo(f"\n  Pending courses linked to '{name}':")
        for c in pending:
            cp = c["properties"]
            cn_parts = cp.get("Name", {}).get("title", [])
            cn = cn_parts[0]["plain_text"] if cn_parts else "?"
            code = cp.get("Code", {}).get("select", {}).get("name", "")
            mcs = cp.get("MCs", {}).get("number", 0)
            grade = cp.get("Grade", {}).get("select", {}).get("name", "?")
            typer.echo(f"    {code}  {cn}  ({mcs} MCs)  [{grade}]")

        want = questionary.confirm(f"\nProject GPA for '{name}'?", style=_STYLE).ask()
        if not want:
            typer.echo("")
            continue

        hypothetical: list[tuple[str, float]] = []
        for c in pending:
            cp = c["properties"]
            cn_parts = cp.get("Name", {}).get("title", [])
            cn = cn_parts[0]["plain_text"] if cn_parts else "?"
            code = cp.get("Code", {}).get("select", {}).get("name", "")
            mcs = float(cp.get("MCs", {}).get("number") or 0)
            label = f"{code}  {cn}  ({mcs:.0f} MCs)"
            grade = questionary.select(
                f"Hypothetical grade for {label}:",
                choices=grade_choices,
                style=_STYLE,
            ).ask()
            if grade:
                hypothetical.append((grade, mcs))

        if hypothetical:
            proj = project_gpa(weighted_gp, counted_mcs, hypothetical)
            added_mcs = sum(m for g, m in hypothetical if g in GRADE_POINTS)
            typer.echo(
                f"\n  Projected GPA: {proj:.4f}"
                f"  ({counted_mcs + added_mcs:.0f} counted MCs)\n"
            )


@app.command()
def create_todos(
    timezone: str = typer.Option(DEFAULT_TIMEZONE, help="Timezone for class times"),
) -> None:
    """Interactively create Course To-Dos for every session of a class."""
    tz = ZoneInfo(timezone)

    course_rows = fetch_courses_db()
    active_courses = [
        r
        for r in course_rows
        if r["properties"].get("Status", {}).get("formula", {}).get("string", "")
        == "In progress"
    ]
    if not active_courses:
        typer.echo("No courses currently in progress.")
        raise typer.Exit()

    _cancel = "↩  Cancel"
    _back = "← Back"

    label_to_course: dict[str, dict[str, Any]] = {}
    for row in active_courses:
        props = row["properties"]
        name_parts = props.get("Name", {}).get("title", [])
        name = name_parts[0]["plain_text"] if name_parts else "?"
        code = props.get("Code", {}).get("select", {}).get("name", "")
        lbl = f"{code}  —  {name}" if code else name
        label_to_course[lbl] = row

    # Pre-fetch all classes once; filter per-course inside the step loop.
    all_classes = fetch_classes_ds()

    def _class_label(row: dict[str, Any]) -> str:
        props = row["properties"]
        title_parts = props.get("Title", {}).get("title", [])
        title = title_parts[0]["plain_text"] if title_parts else "?"
        ct = props.get("Type", {}).get("select", {}).get("name", "")
        day = props.get("Day", {}).get("select", {}).get("name", "")
        weeks_raw = props.get("Weeks", {}).get("select", {}).get("name", "")
        weeks = {
            "All": "every week",
            "Odd": "odd weeks",
            "Even": "even weeks",
            "Single": "once",
        }.get(weeks_raw, weeks_raw)
        time_slot = props.get("Time Slot", {}).get("formula", {}).get("string", "")
        parts = [p for p in [ct, day, weeks, time_slot] if p]
        return f"{title}  ({', '.join(parts)})"

    # Accumulated answers — persist across back-navigation.
    chosen_course_label: str | None = None
    course_row: dict[str, Any] | None = None
    course_id: str | None = None
    label_to_class: dict[str, dict[str, Any]] = {}
    chosen_class_label: str | None = None
    class_row: dict[str, Any] | None = None
    cls_type: str = ""
    chosen_type: str | None = None
    title_prefix: str | None = None
    chosen_priority: str | None = None
    shared_url: str | None = None

    step = 0
    while step < 6:
        if step == 0:
            ans: str | None = questionary.select(
                "Select course:",
                choices=[*sorted(label_to_course), _cancel],
                default=chosen_course_label,
                style=_STYLE,
            ).ask()
            if not ans or ans == _cancel:
                raise typer.Exit()
            chosen_course_label = ans
            course_row = label_to_course[ans]
            course_id = course_row["id"]
            step += 1

        elif step == 1:
            assert course_id is not None
            course_classes = [
                r
                for r in all_classes
                if any(
                    c["id"] == course_id
                    for c in r["properties"].get("Course", {}).get("relation", [])
                )
            ]
            if not course_classes:
                typer.echo("No classes found for this course.")
                raise typer.Exit()
            label_to_class = {_class_label(r): r for r in course_classes}
            default_cls = (
                chosen_class_label if chosen_class_label in label_to_class else None
            )
            ans = questionary.select(
                "Select class:",
                choices=[*sorted(label_to_class), _back, _cancel],
                default=default_cls,
                style=_STYLE,
            ).ask()
            if not ans or ans == _cancel:
                raise typer.Exit()
            if ans == _back:
                step -= 1
                continue
            chosen_class_label = ans
            class_row = label_to_class[ans]
            cls_type = (
                class_row["properties"]
                .get("Type", {})
                .get("select", {})
                .get("name", "")
            )
            step += 1

        elif step == 2:
            default_type = suggested_todo_type(cls_type)
            type_choices = [default_type] + [t for t in TODO_TYPES if t != default_type]
            default_ct = chosen_type if chosen_type in type_choices else default_type
            ans = questionary.select(
                "To-do type:",
                choices=[*type_choices, _back, _cancel],
                default=default_ct,
                style=_STYLE,
            ).ask()
            if not ans or ans == _cancel:
                raise typer.Exit()
            if ans == _back:
                step -= 1
                continue
            chosen_type = ans
            step += 1

        elif step == 3:
            ans = questionary.text(
                "Title prefix (Ctrl+C to go back):",
                default=title_prefix
                if title_prefix is not None
                else (chosen_type or ""),
                style=_STYLE,
            ).ask()
            if ans is None:  # Ctrl+C → go back
                step -= 1
                continue
            if not ans:
                raise typer.Exit()
            title_prefix = ans
            step += 1

        elif step == 4:
            default_prio = (
                chosen_priority if chosen_priority in PRIORITIES else "Medium"
            )
            ans = questionary.select(
                "Priority:",
                choices=[*PRIORITIES, _back, _cancel],
                default=default_prio,
                style=_STYLE,
            ).ask()
            if not ans or ans == _cancel:
                raise typer.Exit()
            if ans == _back:
                step -= 1
                continue
            chosen_priority = ans
            step += 1

        elif step == 5:
            ans = questionary.text(
                "URL (Enter to skip, Ctrl+C to go back):",
                default=shared_url or "",
                style=_STYLE,
            ).ask()
            if ans is None:  # Ctrl+C → go back
                step -= 1
                continue
            shared_url = ans.strip() if ans.strip() else None
            step += 1

    assert course_id is not None
    assert course_row is not None
    assert class_row is not None
    assert chosen_type is not None
    assert title_prefix is not None
    assert chosen_priority is not None

    # Fetch semester schedule: end date, exam week, recess/reading weeks
    sem_relations = course_row["properties"].get("Semester", {}).get("relation", [])
    fallback_end: str | None = None
    exam_week_start: str | None = None
    skip_ranges: list[tuple[date, date]] = []
    skip_range_labels: list[tuple[date, date, str]] = []
    if sem_relations:
        sem_page = fetch_page(sem_relations[0]["id"])
        sp = sem_page["properties"]
        fallback_end = sp.get("Semester Dates", {}).get("date", {}).get("end")
        exam_week_start = sp.get("Examination Weeks", {}).get("date", {}).get("start")
        for week_label, key in [
            ("Recess Week", "Recess Week"),
            ("Reading Week", "Reading Week"),
        ]:
            dt = (sp.get(key) or {}).get("date") or {}
            if dt.get("start") and dt.get("end"):
                r_start = date.fromisoformat(dt["start"][:10])
                r_end = date.fromisoformat(dt["end"][:10])
                skip_ranges.append((r_start, r_end))
                skip_range_labels.append((r_start, r_end, week_label))

    kept, skipped = generate_occurrences(
        class_row, tz, fallback_end, exam_week_start, skip_ranges
    )
    if not kept:
        typer.echo("Could not compute occurrences for this class.")
        raise typer.Exit()

    items: list[tuple[str, datetime]] = [
        (f"{title_prefix} {i + 1}", occ) for i, occ in enumerate(kept)
    ]

    def _skip_reason(d: date) -> str:
        for rs, re_, lbl in skip_range_labels:
            if rs <= d <= re_:
                return lbl
        return "Skipped"

    all_display: list[tuple[bool, str, datetime]] = [
        (False, item_name, occ) for item_name, occ in items
    ] + [(True, _skip_reason(occ.date()), occ) for occ in skipped]
    all_display.sort(key=lambda r: r[2])

    typer.echo("")
    col_name = max((len(item_name) for _, item_name, _ in all_display), default=4)
    col_type = max(len(chosen_type), 4)
    header = (
        f"  {'#':>4}  {'Name':<{col_name}}  {'Type':<{col_type}}"
        f"  {'Due Date':<16}  {'Priority':<8}  URL"
    )
    typer.echo(header)
    typer.echo("  " + "-" * (len(header) - 2))
    kept_idx = 0
    for is_skip, display_name, occ in all_display:
        if shared_url and len(shared_url) > 29:
            url_col = shared_url[:28] + "…"
        else:
            url_col = shared_url or "—"
        if is_skip:
            typer.echo(
                f"  {'skip':>4}  {f'({display_name})':<{col_name}}  {'—':<{col_type}}"
                f"  {occ.strftime('%Y-%m-%d %H:%M'):<16}  {'—':<8}  —"
            )
        else:
            kept_idx += 1
            typer.echo(
                f"  {kept_idx:>4}  {display_name:<{col_name}}"
                f"  {chosen_type:<{col_type}}"
                f"  {occ.strftime('%Y-%m-%d %H:%M'):<16}"
                f"  {chosen_priority:<8}  {url_col}"
            )
    typer.echo("")

    confirm: bool | None = questionary.confirm(
        f"Create {len(items)} to-do(s)?",
        default=True,
        style=_STYLE,
    ).ask()
    if not confirm:
        typer.echo("Cancelled.")
        raise typer.Exit()

    db_id = get_course_todos_db_id()
    type_to_template = fetch_course_todos_templates()
    template_id: str | None = type_to_template.get(chosen_type)
    for item_name, occ in items:
        new_page = create_course_todo(
            db_id=db_id,
            name=item_name,
            course_id=course_id,
            todo_type=chosen_type,
            priority=chosen_priority,
            due_dt=occ,
            url=shared_url,
        )
        if template_id:
            apply_template_to_page(template_id, new_page["id"])
        typer.echo(f"  created  {item_name}  ({occ.strftime('%Y-%m-%d %H:%M')})")

    typer.echo(f"\nDone — {len(items)} to-dos created.")


def _slugify(text: str) -> str:
    """Convert arbitrary text to a kebab-case GitHub repo name slug."""
    text = text.lower().replace("/", "-").replace(" ", "-")
    text = re.sub(r"[^a-z0-9-]", "", text)
    return re.sub(r"-{2,}", "-", text).strip("-")


def _fill_index(template: str, notion_url: str, course_website: str) -> str:
    """Fill Notion and Course Website rows in the _index template."""
    lines = []
    for line in template.splitlines(keepends=True):
        s = line.lstrip()
        if s.startswith("| Notion") and s.count("|") >= 2:
            lines.append(f"| Notion         | [Course Page]({notion_url}) |\n")
        elif s.startswith("| Course Website") and s.count("|") >= 2:
            lines.append(f"| Course Website | {course_website} |\n")
        else:
            lines.append(line)
    return "".join(lines)


def _update_github_row(path: Path, github_url: str) -> None:
    """Replace the empty GitHub Repo cell in an existing _index.md."""
    text = path.read_text()
    lines = []
    for line in text.splitlines(keepends=True):
        s = line.lstrip()
        if s.startswith("| GitHub Repo") and s.count("|") >= 2:
            lines.append(f"| GitHub Repo    | {github_url} |\n")
        else:
            lines.append(line)
    path.write_text("".join(lines))


@app.command()
def setup_semester() -> None:
    """Bootstrap Obsidian dirs, tutorial files, and GitHub repos for a semester."""
    vault = Path(os.environ.get("OBSIDIAN_VAULT", "~/Documents/Obsidian")).expanduser()
    template_index = vault / "Templates" / "School" / "course" / "_index.md"
    template_tutorial = (
        vault / "Templates" / "School" / "course" / "tutorials" / "Tutorial.md"
    )

    if not template_index.exists():
        typer.echo(f"Template not found: {template_index}")
        raise typer.Exit(1)
    if not template_tutorial.exists():
        typer.echo(f"Template not found: {template_tutorial}")
        raise typer.Exit(1)

    index_template_text = template_index.read_text()
    tutorial_template_body = template_tutorial.read_text()

    # --- Semester selection ---
    sem_rows = fetch_semesters_db()
    sem_label_to_row: dict[str, dict[str, Any]] = {}
    for row in sem_rows:
        parts = row["properties"].get("Semester", {}).get("title", [])
        title = parts[0]["plain_text"] if parts else "?"
        uni = row["properties"].get("University", {}).get("select", {}).get("name", "")
        lbl = f"{title}  ({uni})" if uni else title
        sem_label_to_row[lbl] = row

    chosen_sem_label: str | None = questionary.select(
        "Select semester:",
        choices=sorted(sem_label_to_row),
        style=_STYLE,
    ).ask()
    if not chosen_sem_label:
        raise typer.Exit()

    sem_row = sem_label_to_row[chosen_sem_label]
    sem_title_raw: str = (
        sem_row["properties"]
        .get("Semester", {})
        .get("title", [{}])[0]
        .get("plain_text", "Semester")
    )
    sem_dir = sem_title_raw.split("/")[0].strip()
    university: str = (
        sem_row["properties"].get("University", {}).get("select", {}).get("name", "")
    )
    sem_course_ids = {
        r["id"] for r in sem_row["properties"].get("Courses", {}).get("relation", [])
    }

    all_courses = fetch_courses_db()
    courses = [r for r in all_courses if r["id"] in sem_course_ids]
    if not courses:
        typer.echo("No courses found for this semester.")
        raise typer.Exit()

    typer.echo(f"\nFound {len(courses)} course(s) in {sem_dir}.\n")

    # --- One-time: ensure GitHub Repo property exists on Courses DB ---
    ensure_courses_github_property()

    # --- GitHub username ---
    gh_result = subprocess.run(
        ["gh", "api", "user", "--jq", ".login"],
        capture_output=True,
        text=True,
    )
    gh_user = gh_result.stdout.strip() if gh_result.returncode == 0 else "unknown"

    created: list[str] = []
    skipped: list[str] = []

    for course in courses:
        props = course["properties"]
        course_id: str = course["id"]
        code: str = props.get("Code", {}).get("select", {}).get("name", "") or "UNKNOWN"
        name_parts = props.get("Name", {}).get("title", [])
        name: str = name_parts[0]["plain_text"] if name_parts else "Unknown"
        course_website: str = props.get("Course Website", {}).get("url") or ""

        code_safe = code.replace("/", "_")
        dir_name = f"{code_safe} {name}"
        repo_name = _slugify(f"{code}-{name}")
        notion_url = "https://www.notion.so/" + course_id.replace("-", "")
        vault_course = vault / "School" / sem_dir / dir_name
        repo_dir = Path("~/Developer").expanduser() / repo_name
        github_url = f"https://github.com/{gh_user}/{repo_name}"

        typer.echo(f"{'=' * 60}")
        typer.echo(f"  {code}  —  {name}")
        typer.echo(f"{'=' * 60}")

        # b. Create Obsidian dirs
        for subdir in ("lectures", "tutorials", "cheatsheets"):
            (vault_course / subdir).mkdir(parents=True, exist_ok=True)

        # c. Generate _index.md
        index_path = vault_course / "_index.md"
        if index_path.exists():
            typer.echo("  _index.md already exists — skipping.")
        else:
            index_fm = (
                "---\n"
                f"code: {code}\n"
                f"name: {name}\n"
                f"semester: {sem_dir}\n"
                f"university: {university}\n"
                "---\n\n"
            )
            content = index_fm + _fill_index(
                index_template_text, notion_url, course_website
            )
            index_path.write_text(content)
            typer.echo("  created _index.md")

        # d. Create tutorial Obsidian files
        tutorials = fetch_course_todos_for_course(course_id, "Tutorial")
        for i, todo in enumerate(tutorials):
            todo_props = todo["properties"]
            todo_name_parts = todo_props.get("Name", {}).get("title", [])
            todo_name: str = (
                todo_name_parts[0]["plain_text"]
                if todo_name_parts
                else f"Tutorial {i + 1}"
            )
            due_raw = (todo_props.get("Due Date") or {}).get("date", {})
            due_date: str = (due_raw.get("start") or "")[:10] if due_raw else ""
            tutorial_path = vault_course / "tutorials" / f"{todo_name}.md"
            if tutorial_path.exists():
                typer.echo(f"  {todo_name}.md already exists — skipping.")
                continue

            frontmatter = (
                f"---\ncode: {code}\ndate: {due_date}\ntags:\n  - tutorial\n---\n\n"
            )
            tutorial_path.write_text(frontmatter + tutorial_template_body)
            typer.echo(f"  created tutorials/{todo_name}.md")

        # e. Create GitHub repo
        if repo_dir.exists():
            typer.echo(f"  {repo_dir} already exists — skipping repo creation.")
        else:
            confirm_repo: bool | None = questionary.confirm(
                f"  Create GitHub repo '{repo_name}'?",
                default=True,
                style=_STYLE,
            ).ask()
            if confirm_repo:
                package_name = repo_name.replace("-", "_")
                author = (
                    subprocess.run(
                        ["git", "config", "user.name"],
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    or "Namit Deb"
                )
                email = (
                    subprocess.run(
                        ["git", "config", "user.email"],
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    or ""
                )
                subprocess.run(
                    [
                        "uvx",
                        "copier",
                        "copy",
                        "--trust",
                        "--data",
                        f"project_name={repo_name}",
                        "--data",
                        f"package_name={package_name}",
                        "--data",
                        f"description={name}",
                        "--data",
                        f"author_name={author}",
                        "--data",
                        f"author_email={email}",
                        "--data",
                        f"github_user={gh_user}",
                        "gh:namitdeb739/python-template",
                        str(repo_dir),
                    ],
                    check=True,
                )
                subprocess.run(
                    ["just", "init-remote", "private"],
                    cwd=repo_dir,
                    check=True,
                )
                typer.echo(f"  created {github_url}")

                # f. Update _index.md GitHub Repo cell
                _update_github_row(index_path, github_url)
                typer.echo("  updated _index.md with GitHub URL")

                # g. Update Notion
                update_course_github_url(course_id, github_url)
                typer.echo("  updated Notion course page")

                created.append(repo_name)
            else:
                skipped.append(repo_name)

        typer.echo("")

    # --- Create semester base file ---
    sem_base_path = vault / "School" / sem_dir / f"{sem_dir}.base"
    if sem_base_path.exists():
        typer.echo(f"{sem_dir}.base already exists — skipping.")
    else:
        sem_base_content = (
            f"filters:\n"
            f"  and:\n"
            f'    - file.folder.contains("School/{sem_dir}/")\n'
            f'    - file.hasTag("tutorial")\n'
            f"properties:\n"
            f"  note.code:\n"
            f"    displayName: Course\n"
            f"  note.date:\n"
            f"    displayName: Date\n"
            f"views:\n"
            f"  - type: table\n"
            f"    name: Tutorials\n"
            f"    order:\n"
            f"      - file.name\n"
            f"      - code\n"
            f"      - date\n"
            f"    sort:\n"
            f"      - property: code\n"
            f"        direction: ASC\n"
            f"      - property: date\n"
            f"        direction: ASC\n"
        )
        sem_base_path.write_text(sem_base_content)
        typer.echo(f"created {sem_dir}.base")

    typer.echo(f"\nDone — {len(created)} repo(s) created, {len(skipped)} skipped.")


@app.command()
def wise_sync(
    days: int = typer.Option(7, help="Look-back window in days"),
    since: str = typer.Option(
        "", "--since", help="Start date YYYY-MM-DD (overrides --days)"
    ),
    profile_id: int = typer.Option(
        0, "--profile-id", help="Wise profile ID (auto-detected if 0)"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print without writing to Notion"
    ),
) -> None:
    """Sync recent Wise transactions to the Notion Finance DB."""
    from notion_automations.finance import upsert_transaction
    from notion_automations.notion import get_notion_client
    from notion_automations.wise import WiseClient

    wise_token = os.environ.get("WISE_API_TOKEN")
    if not wise_token:
        typer.echo("WISE_API_TOKEN environment variable not set.")
        raise typer.Exit(1)

    until = datetime.now(UTC)
    start = (
        datetime.fromisoformat(since).replace(tzinfo=UTC)
        if since
        else until - timedelta(days=days)
    )

    wise = WiseClient(wise_token)
    pid = profile_id or wise.get_personal_profile_id()

    notion = get_notion_client()

    typer.echo(
        f"Fetching transactions {start.date()} → {until.date()} (profile {pid})…"
    )
    transactions = wise.get_all_transactions(pid, start, until)

    if not transactions:
        typer.echo("No transactions found.")
        return

    col = min(
        max(len(t.merchant or t.reference or "Unknown") for t in transactions), 40
    )
    created_count = skipped_count = 0

    for txn in transactions:
        label = (txn.merchant or txn.reference or "Unknown")[:col]
        line = (
            f"  {txn.date.strftime('%Y-%m-%d %H:%M')}  "
            f"{label:<{col}}  {txn.direction:<6}  SGD {txn.amount:.2f}"
        )
        if dry_run:
            typer.echo(line)
            continue
        was_created, _ = upsert_transaction(notion, txn)
        status = "created" if was_created else "skip   "
        if was_created:
            created_count += 1
        else:
            skipped_count += 1
        typer.echo(f"  {status} {line.lstrip()}")

    if not dry_run:
        typer.echo(f"\nDone — {created_count} created, {skipped_count} skipped.")
