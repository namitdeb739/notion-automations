"""Notion write helpers for the personal finance Transactions database."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from notion_client import Client

    from notion_automations.wise import WiseTransaction

# Database ID — used as parent when creating pages.
_FINANCE_DB_ID = "34f9080d-a147-80c1-ba01-e9fbfd180524"
# Data source ID — used for querying (notion-client v3 removed databases.query).
_FINANCE_DS_ID = "34f9080d-a147-8008-9a9e-000b8a398d11"


def transaction_to_notion_props(txn: WiseTransaction) -> dict[str, Any]:
    label = txn.merchant or txn.reference or "Unknown"
    name = f"{label} — {txn.amount:.2f} SGD"
    props: dict[str, Any] = {
        "Name": {"title": [{"text": {"content": name}}]},
        "Date": {"date": {"start": txn.date.isoformat()}},
        "Amount": {"number": float(txn.amount)},
        "Direction": {"select": {"name": txn.direction}},
        "Source": {"select": {"name": "Wise"}},
        "External ID": {"rich_text": [{"text": {"content": txn.id}}]},
    }
    if txn.merchant:
        props["Merchant"] = {"rich_text": [{"text": {"content": txn.merchant}}]}
    if txn.original_amount and txn.original_currency:
        note = f"{txn.original_amount} {txn.original_currency}"
        if txn.exchange_rate:
            note += f" @ {txn.exchange_rate}"
        props["Notes"] = {"rich_text": [{"text": {"content": note}}]}
    return props


def transaction_exists(notion: Client, external_id: str) -> bool:
    resp = cast(
        "dict[str, Any]",
        notion.data_sources.query(
            _FINANCE_DS_ID,
            filter={"property": "External ID", "rich_text": {"equals": external_id}},
            page_size=1,
        ),
    )
    return len(resp.get("results", [])) > 0


def upsert_transaction(notion: Client, txn: WiseTransaction) -> tuple[bool, str]:
    """Create transaction page if it doesn't already exist.

    Returns (created, page_id). page_id is empty string when skipped.
    """
    if transaction_exists(notion, txn.id):
        return False, ""
    props = transaction_to_notion_props(txn)
    page = cast(
        "dict[str, Any]",
        notion.pages.create(parent={"database_id": _FINANCE_DB_ID}, properties=props),
    )
    return True, str(page["id"])
