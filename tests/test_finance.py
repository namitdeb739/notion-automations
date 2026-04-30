from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

from notion_automations.finance import (
    transaction_exists,
    transaction_to_notion_props,
    upsert_transaction,
)
from notion_automations.wise import WiseTransaction


def _txn(**kwargs: Any) -> WiseTransaction:
    defaults: dict[str, Any] = {
        "id": "CARD-001",
        "date": datetime(2026, 4, 15, 10, 30, tzinfo=UTC),
        "amount": Decimal("25.50"),
        "currency": "SGD",
        "direction": "Debit",
        "merchant": "McDonald's",
        "reference": "McDonald's Singapore",
        "transaction_type": "CARD",
        "original_amount": None,
        "original_currency": None,
        "exchange_rate": None,
    }
    defaults.update(kwargs)
    return WiseTransaction(**defaults)


# --- transaction_to_notion_props ---


def test_props_debit_with_merchant() -> None:
    props = transaction_to_notion_props(_txn())
    assert props["Name"]["title"][0]["text"]["content"] == "McDonald's — 25.50 SGD"
    assert props["Amount"]["number"] == -25.50
    assert props["Direction"]["select"]["name"] == "Debit"
    assert props["Source"]["select"]["name"] == "Wise"
    assert props["External ID"]["rich_text"][0]["text"]["content"] == "CARD-001"
    assert props["Merchant"]["rich_text"][0]["text"]["content"] == "McDonald's"
    assert props["Date"]["date"]["start"] == "2026-04-15T10:30:00+00:00"


def test_props_credit_no_merchant() -> None:
    props = transaction_to_notion_props(
        _txn(
            direction="Credit",
            merchant=None,
            reference="Top up",
            amount=Decimal("500.00"),
        )
    )
    assert props["Direction"]["select"]["name"] == "Credit"
    assert props["Amount"]["number"] == 500.00
    assert "500.00" in props["Name"]["title"][0]["text"]["content"]
    assert "Merchant" not in props  # omitted when None


def test_props_name_falls_back_to_reference() -> None:
    props = transaction_to_notion_props(_txn(merchant=None, reference="Netflix"))
    assert props["Name"]["title"][0]["text"]["content"] == "Netflix — 25.50 SGD"


def test_props_name_falls_back_to_unknown() -> None:
    props = transaction_to_notion_props(_txn(merchant=None, reference=None))
    assert props["Name"]["title"][0]["text"]["content"] == "Unknown — 25.50 SGD"


def test_props_notes_foreign_currency_with_rate() -> None:
    props = transaction_to_notion_props(
        _txn(
            original_amount=Decimal("9.90"),
            original_currency="GBP",
            exchange_rate=Decimal("0.5795"),
        )
    )
    assert props["Notes"]["rich_text"][0]["text"]["content"] == "9.90 GBP @ 0.5795"


def test_props_notes_foreign_currency_no_rate() -> None:
    props = transaction_to_notion_props(
        _txn(original_amount=Decimal("9.90"), original_currency="GBP")
    )
    assert props["Notes"]["rich_text"][0]["text"]["content"] == "9.90 GBP"


def test_props_notes_absent_for_sgd_transaction() -> None:
    props = transaction_to_notion_props(_txn())
    assert "Notes" not in props


# --- transaction_exists ---


def test_transaction_exists_true() -> None:
    notion = MagicMock()
    notion.data_sources.query.return_value = {"results": [{"id": "page-1"}]}
    assert transaction_exists(notion, "CARD-001") == "page-1"


def test_transaction_exists_false() -> None:
    notion = MagicMock()
    notion.data_sources.query.return_value = {"results": []}
    assert transaction_exists(notion, "CARD-001") is None


# --- upsert_transaction ---


def test_upsert_updates_existing_amount() -> None:
    notion = MagicMock()
    notion.data_sources.query.return_value = {"results": [{"id": "existing"}]}
    created, page_id = upsert_transaction(notion, _txn())
    assert created is False
    assert page_id == "existing"
    notion.pages.create.assert_not_called()
    notion.pages.update.assert_called_once()
    call_kwargs = notion.pages.update.call_args.kwargs
    assert call_kwargs["page_id"] == "existing"
    assert call_kwargs["properties"]["Amount"]["number"] == -25.50  # Debit → negative


def test_upsert_creates_new() -> None:
    notion = MagicMock()
    notion.data_sources.query.return_value = {"results": []}
    notion.pages.create.return_value = {"id": "new-page"}
    created, page_id = upsert_transaction(notion, _txn())
    assert created is True
    assert page_id == "new-page"
    notion.pages.create.assert_called_once()


def test_upsert_uses_hardcoded_db_id() -> None:
    notion = MagicMock()
    notion.data_sources.query.return_value = {"results": []}
    notion.pages.create.return_value = {"id": "x"}
    upsert_transaction(notion, _txn())
    call_kwargs = notion.pages.create.call_args
    db_id = call_kwargs.kwargs["parent"]["database_id"]
    assert db_id == "34f9080d-a147-80c1-ba01-e9fbfd180524"
