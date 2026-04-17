"""Notion API integration for school dashboard databases."""

import os
from typing import Any, cast

from notion_client import Client

# Stable data-source IDs for this workspace (override via env vars if needed)
_COURSES_DS_ID = "33d9080d-a147-80f1-91a1-000b0a393b27"
_SEMESTERS_DS_ID = "33d9080d-a147-8048-819c-000b3f1a4d1d"


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
