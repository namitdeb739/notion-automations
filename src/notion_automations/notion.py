"""Notion API integration for school dashboard databases."""

import os
from datetime import datetime
from typing import Any, cast

from notion_client import Client

# Stable data-source IDs for this workspace (override via env vars if needed)
_COURSES_DS_ID = "33d9080d-a147-80f1-91a1-000b0a393b27"
_COURSES_DB_ID = "33d9080d-a147-806d-8b04-c0f0516bac16"
_SEMESTERS_DS_ID = "33d9080d-a147-8048-819c-000b3f1a4d1d"
_GPA_DS_ID = "33d9080d-a147-80a5-b7ff-000b2a470190"
_CLASSES_DS_ID = "33d9080d-a147-809a-a8d6-000b74ccf447"
_COURSE_TODOS_DS_ID = "33d9080d-a147-8093-ab2d-000bd2b04c53"


def get_notion_client() -> Client:
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise RuntimeError("NOTION_TOKEN environment variable not set.")
    return Client(auth=token)


def _query_data_source(notion: Client, ds_id: str) -> list[dict[str, Any]]:
    """Paginate through all rows of a Notion data source."""
    results: list[dict[str, Any]] = []
    next_cursor: str | None = None
    while True:
        resp = cast(
            "dict[str, Any]",
            notion.data_sources.query(ds_id, page_size=100, start_cursor=next_cursor),
        )
        results.extend(resp["results"])
        if not resp.get("has_more"):
            break
        next_cursor = resp["next_cursor"]
    return results


def fetch_classes_db(db_id: str) -> list[dict[str, Any]]:
    notion = get_notion_client()
    db = cast("dict[str, Any]", notion.databases.retrieve(database_id=db_id))
    if db.get("data_sources"):
        ds_id: str = db["data_sources"][0]["id"]
        return _query_data_source(notion, ds_id)
    # Fallback for classic (non-data-source) DB
    results: list[dict[str, Any]] = []
    next_cursor: str | None = None
    while True:
        kwargs: dict[str, Any] = {"database_id": db_id, "page_size": 100}
        if next_cursor:
            kwargs["start_cursor"] = next_cursor
        resp = cast(
            "dict[str, Any]",
            notion.databases.query(**kwargs),  # type: ignore[attr-defined]
        )
        results.extend(resp["results"])
        if not resp.get("has_more"):
            break
        next_cursor = resp["next_cursor"]
    return results


def fetch_courses_db(ds_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch all rows from the Courses database."""
    notion = get_notion_client()
    resolved: str = ds_id or os.environ.get("NOTION_COURSES_DB_ID") or _COURSES_DS_ID
    return _query_data_source(notion, resolved)


def fetch_semesters_db(ds_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch all rows from the Semesters database."""
    notion = get_notion_client()
    resolved: str = (
        ds_id or os.environ.get("NOTION_SEMESTERS_DB_ID") or _SEMESTERS_DS_ID
    )
    return _query_data_source(notion, resolved)


def fetch_gpa_db(ds_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch all rows from the Grade Point Average database."""
    notion = get_notion_client()
    resolved: str = ds_id or os.environ.get("NOTION_GPA_DB_ID") or _GPA_DS_ID
    return _query_data_source(notion, resolved)


def fetch_page(page_id: str) -> dict[str, Any]:
    """Fetch a single Notion page by ID."""
    notion = get_notion_client()
    return cast("dict[str, Any]", notion.pages.retrieve(page_id=page_id))


def fetch_classes_ds(ds_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch all rows from the Classes data source."""
    notion = get_notion_client()
    resolved: str = ds_id or os.environ.get("NOTION_CLASSES_DS_ID") or _CLASSES_DS_ID
    return _query_data_source(notion, resolved)


def fetch_course_todos_db(ds_id: str | None = None) -> list[dict[str, Any]]:
    """Fetch all rows from the Course To-Dos data source."""
    notion = get_notion_client()
    resolved: str = (
        ds_id or os.environ.get("NOTION_COURSE_TODOS_DS_ID") or _COURSE_TODOS_DS_ID
    )
    return _query_data_source(notion, resolved)


def get_course_todos_db_id(ds_id: str | None = None) -> str:
    """Return the database ID for Course To-Dos (required for pages.create).

    Tries NOTION_COURSE_TODOS_DB_ID env var first, then discovers it from the
    data source by inspecting the parent of the first existing page.
    """
    env_db_id = os.environ.get("NOTION_COURSE_TODOS_DB_ID")
    if env_db_id:
        return env_db_id
    notion = get_notion_client()
    resolved: str = ds_id or _COURSE_TODOS_DS_ID
    resp = cast(
        "dict[str, Any]",
        notion.data_sources.query(resolved, page_size=1),
    )
    results: list[dict[str, Any]] = resp.get("results", [])
    if not results:
        raise RuntimeError(
            "No pages in Course To-Dos data source; "
            "set NOTION_COURSE_TODOS_DB_ID env var."
        )
    db_id: str | None = results[0].get("parent", {}).get("database_id")
    if not db_id:
        raise RuntimeError(
            "Cannot determine Course To-Dos database ID; "
            "set NOTION_COURSE_TODOS_DB_ID env var."
        )
    return db_id


_TEMPLATE_BLOCK_READONLY = frozenset(
    {
        "id",
        "object",
        "parent",
        "created_time",
        "created_by",
        "last_edited_time",
        "last_edited_by",
        "has_children",
        "archived",
        "in_trash",
    }
)


def _clean_block(block: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in block.items() if k not in _TEMPLATE_BLOCK_READONLY}


def _list_children(notion: Client, block_id: str) -> list[dict[str, Any]]:
    resp = cast(
        "dict[str, Any]",
        notion.blocks.children.list(block_id, page_size=100),
    )
    return cast("list[dict[str, Any]]", resp.get("results", []))


def fetch_course_todos_templates(ds_id: str | None = None) -> dict[str, str]:
    """Return template name → template page ID for the Course To-Dos database."""
    notion = get_notion_client()
    resolved: str = ds_id or _COURSE_TODOS_DS_ID
    resp = cast(
        "dict[str, Any]",
        notion.data_sources.list_templates(resolved),
    )
    return {t["name"]: t["id"] for t in resp.get("templates", [])}


def apply_template_to_page(template_id: str, page_id: str) -> None:
    """Copy blocks from a template page onto a newly-created page.

    Uses separate append calls per nesting level (no inline children) because
    the Notion API rejects inline children for certain block types.
    """
    notion = get_notion_client()

    src_top = _list_children(notion, template_id)
    if not src_top:
        return

    # Level 1: append top-level blocks without inline children.
    resp1 = cast(
        "dict[str, Any]",
        notion.blocks.children.append(
            page_id, children=[_clean_block(b) for b in src_top]
        ),
    )
    new_top = cast("list[dict[str, Any]]", resp1.get("results", []))

    # Level 2: for each top-level block that had children, append them separately.
    for src_block, new_block in zip(src_top, new_top, strict=False):
        if not src_block.get("has_children"):
            continue
        src_children = _list_children(notion, src_block["id"])
        resp2 = cast(
            "dict[str, Any]",
            notion.blocks.children.append(
                new_block["id"],
                children=[_clean_block(c) for c in src_children],
            ),
        )
        new_children = cast("list[dict[str, Any]]", resp2.get("results", []))

        # Level 3: same pattern for grandchildren.
        for src_child, new_child in zip(src_children, new_children, strict=False):
            if src_child.get("has_children"):
                grandchildren = _list_children(notion, src_child["id"])
                if grandchildren:
                    notion.blocks.children.append(
                        new_child["id"],
                        children=[_clean_block(g) for g in grandchildren],
                    )


def fetch_course_todos_for_course(
    course_id: str, todo_type: str = "Tutorial"
) -> list[dict[str, Any]]:
    """Fetch to-dos of a given type for a course, sorted by due date."""
    rows = fetch_course_todos_db()
    result: list[dict[str, Any]] = []
    for row in rows:
        props = row["properties"]
        rel_ids = [r["id"] for r in props.get("Course", {}).get("relation", [])]
        if course_id not in rel_ids:
            continue
        row_type = props.get("Type", {}).get("select", {}).get("name", "")
        if row_type == todo_type:
            result.append(row)
    return sorted(
        result,
        key=lambda r: (
            (r["properties"].get("Due Date") or {}).get("date", {}).get("start") or ""
        ),
    )


def ensure_courses_github_property() -> None:
    """Add a 'GitHub Repo' URL property to the Courses database if absent."""
    notion = get_notion_client()
    db = cast(
        "dict[str, Any]",
        notion.databases.retrieve(database_id=_COURSES_DB_ID),
    )
    if "GitHub Repo" not in db.get("properties", {}):
        notion.databases.update(
            database_id=_COURSES_DB_ID,
            properties={"GitHub Repo": {"url": {}}},
        )


def update_course_github_url(course_id: str, github_url: str) -> None:
    """Set the GitHub Repo URL property on a Courses page."""
    notion = get_notion_client()
    notion.pages.update(
        page_id=course_id,
        properties={"GitHub Repo": {"url": github_url}},
    )


def create_course_todo(
    db_id: str,
    name: str,
    course_id: str,
    todo_type: str,
    priority: str,
    due_dt: datetime,
    url: str | None = None,
) -> dict[str, Any]:
    """Create a single Course To-Do page in Notion."""
    notion = get_notion_client()
    props: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Course": {"relation": [{"id": course_id}]},
        "Type": {"select": {"name": todo_type}},
        "Priority": {"select": {"name": priority}},
        "Due Date": {"date": {"start": due_dt.isoformat()}},
    }
    if url:
        props["URL"] = {"url": url}
    return cast(
        "dict[str, Any]",
        notion.pages.create(parent={"database_id": db_id}, properties=props),
    )
