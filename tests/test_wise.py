from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

from notion_automations.wise import WiseClient, _parse_txn


def _raw_card_debit(
    ref: str = "CARD-abc123",
    value: float = -25.50,
    merchant: str = "McDonald's",
    date: str = "2026-04-15T10:30:00.000Z",
) -> dict[str, Any]:
    return {
        "type": "DEBIT",
        "date": date,
        "amount": {"value": value, "currency": "SGD"},
        "details": {
            "type": "CARD",
            "description": f"{merchant} Singapore",
            "merchant": {"name": merchant},
        },
        "referenceNumber": ref,
    }


def _raw_transfer_credit(
    ref: str = "TRF-xyz789",
    value: float = 1000.0,
    description: str = "Top up",
    date: str = "2026-04-10T08:00:00.000Z",
) -> dict[str, Any]:
    return {
        "type": "CREDIT",
        "date": date,
        "amount": {"value": value, "currency": "SGD"},
        "details": {"type": "TRANSFER", "description": description},
        "referenceNumber": ref,
    }


# --- _parse_txn unit tests ---


def test_parse_txn_debit_card() -> None:
    txn = _parse_txn(_raw_card_debit())
    assert txn.id == "CARD-abc123"
    assert txn.direction == "Debit"
    assert txn.amount == Decimal("25.50")
    assert txn.currency == "SGD"
    assert txn.sgd_equivalent == Decimal("25.50")  # same as amount for SGD
    assert txn.merchant == "McDonald's"
    assert txn.date == datetime(2026, 4, 15, 10, 30, 0, tzinfo=UTC)
    assert txn.transaction_type == "CARD"


def test_parse_txn_credit_transfer() -> None:
    txn = _parse_txn(_raw_transfer_credit())
    assert txn.id == "TRF-xyz789"
    assert txn.direction == "Credit"
    assert txn.amount == Decimal("1000.0")
    assert txn.merchant is None  # no merchant object in transfer
    assert txn.reference == "Top up"


def test_parse_txn_no_merchant_falls_back_to_description() -> None:
    raw = _raw_card_debit()
    del raw["details"]["merchant"]
    txn = _parse_txn(raw)
    assert txn.merchant == "McDonald's Singapore"  # fell back to description


def test_parse_txn_neutral_treated_as_debit() -> None:
    raw = _raw_card_debit()
    raw["type"] = "NEUTRAL"
    txn = _parse_txn(raw)
    assert txn.direction == "Debit"


def test_parse_txn_amount_always_positive() -> None:
    txn = _parse_txn(_raw_card_debit(value=-99.99))
    assert txn.amount == Decimal("99.99")
    assert txn.amount > 0


def test_parse_txn_foreign_card_populates_original_fields() -> None:
    raw = _raw_card_debit(value=-17.20)
    raw["details"]["amount"] = {"value": 9.90, "currency": "GBP"}
    raw["exchangeDetails"] = {"rate": 0.5795}
    txn = _parse_txn(raw)
    assert txn.original_amount == Decimal("9.90")
    assert txn.original_currency == "GBP"
    assert txn.exchange_rate == Decimal("0.5795")


def test_parse_txn_sgd_card_no_original_fields() -> None:
    txn = _parse_txn(_raw_card_debit())
    assert txn.original_amount is None
    assert txn.original_currency is None
    assert txn.exchange_rate is None


def test_parse_txn_foreign_card_no_rate_still_parses() -> None:
    raw = _raw_card_debit(value=-17.20)
    raw["details"]["amount"] = {"value": 9.90, "currency": "GBP"}
    txn = _parse_txn(raw)
    assert txn.original_amount == Decimal("9.90")
    assert txn.original_currency == "GBP"
    assert txn.exchange_rate is None


# --- WiseClient unit tests (mock httpx) ---


def _mock_http(responses: dict[str, Any]) -> MagicMock:
    """Build a mock httpx.Client where .get(url, ...) returns a mock response."""
    mock = MagicMock()

    def get_side_effect(url: str, **kwargs: Any) -> MagicMock:
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json.return_value = responses[url]
        return resp

    mock.get.side_effect = get_side_effect
    return mock


def test_get_personal_profile_id() -> None:
    http = _mock_http(
        {
            "/v1/profiles": [
                {"id": 11, "type": "business"},
                {"id": 42, "type": "personal"},
            ]
        }
    )
    client = WiseClient("tok", _http=http)
    assert client.get_personal_profile_id() == 42


def test_get_personal_profile_id_missing_raises() -> None:
    http = _mock_http({"/v1/profiles": [{"id": 11, "type": "business"}]})
    client = WiseClient("tok", _http=http)
    with pytest.raises(RuntimeError, match="No personal profile"):
        client.get_personal_profile_id()


def test_get_sgd_balance_id() -> None:
    http = _mock_http(
        {
            "/v4/profiles/42/balances": [
                {"id": 100, "currency": "USD"},
                {"id": 101, "currency": "SGD"},
            ]
        }
    )
    client = WiseClient("tok", _http=http)
    assert client.get_sgd_balance_id(42) == 101


def test_get_sgd_balance_id_missing_raises() -> None:
    http = _mock_http({"/v4/profiles/42/balances": [{"id": 100, "currency": "USD"}]})
    client = WiseClient("tok", _http=http)
    with pytest.raises(RuntimeError, match="No SGD balance"):
        client.get_sgd_balance_id(42)


def test_get_statement_parses_transactions() -> None:
    http = _mock_http(
        {
            "/v1/profiles/42/balance-statements/101/statement.json": {
                "transactions": [
                    _raw_card_debit("CARD-001"),
                    _raw_transfer_credit("TRF-001"),
                ]
            }
        }
    )
    client = WiseClient("tok", _http=http)
    txns = client.get_statement(
        42,
        101,
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert len(txns) == 2
    assert txns[0].id == "CARD-001"
    assert txns[1].id == "TRF-001"


def test_get_all_transactions_uses_sgd_balance() -> None:
    http = _mock_http(
        {
            "/v4/profiles/42/balances": [{"id": 101, "currency": "SGD"}],
            "/v1/profiles/42/balance-statements/101/statement.json": {
                "transactions": [_raw_card_debit("CARD-001")]
            },
        }
    )
    client = WiseClient("tok", _http=http)
    txns = client.get_all_transactions(
        42,
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert len(txns) == 1
    assert txns[0].id == "CARD-001"


def test_get_all_transactions_merges_all_balances() -> None:
    eur_txn = {
        "type": "DEBIT",
        "date": "2026-04-23T14:00:00.000Z",
        "amount": {"value": -119.98, "currency": "EUR"},
        "details": {"type": "CARD", "description": "Qh5f7l Deb"},
        "referenceNumber": "CARD-EUR-001",
    }
    http = _mock_http(
        {
            "/v4/profiles/42/balances": [
                {"id": 101, "currency": "SGD"},
                {"id": 102, "currency": "SGD"},  # Wise Jar
                {"id": 103, "currency": "EUR"},
            ],
            "/v1/profiles/42/balance-statements/101/statement.json": {
                "transactions": [_raw_card_debit("CARD-001")]
            },
            "/v1/profiles/42/balance-statements/102/statement.json": {
                "transactions": [_raw_card_debit("CARD-002")]
            },
            "/v1/profiles/42/balance-statements/103/statement.json": {
                "transactions": [eur_txn]
            },
            "/v1/rates": [{"rate": 1.5461, "source": "EUR", "target": "SGD"}],
        }
    )
    client = WiseClient("tok", _http=http)
    txns = client.get_all_transactions(
        42,
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    ids = {t.id for t in txns}
    assert ids == {"CARD-001", "CARD-002", "CARD-EUR-001"}
    eur = next(t for t in txns if t.id == "CARD-EUR-001")
    assert eur.currency == "EUR"
    assert eur.amount == Decimal("119.98")
    assert eur.sgd_equivalent == Decimal("185.50")  # 119.98 * 1.5461, rounded
    assert eur.exchange_rate == Decimal("1.5461")


def test_get_exchange_rate() -> None:
    http = _mock_http(
        {"/v1/rates": [{"rate": 1.5461, "source": "EUR", "target": "SGD"}]}
    )
    client = WiseClient("tok", _http=http)
    at = datetime(2026, 4, 23, 14, 0, tzinfo=UTC)
    rate = client.get_exchange_rate("EUR", "SGD", at)
    assert rate == Decimal("1.5461")


def test_get_all_transactions_deduplicates_across_balances() -> None:
    http = _mock_http(
        {
            "/v4/profiles/42/balances": [
                {"id": 101, "currency": "SGD"},
                {"id": 102, "currency": "SGD"},
            ],
            "/v1/profiles/42/balance-statements/101/statement.json": {
                "transactions": [_raw_card_debit("CARD-001")]
            },
            "/v1/profiles/42/balance-statements/102/statement.json": {
                "transactions": [_raw_card_debit("CARD-001")]  # same txn in both
            },
        }
    )
    client = WiseClient("tok", _http=http)
    txns = client.get_all_transactions(
        42,
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 30, tzinfo=UTC),
    )
    assert len(txns) == 1
    assert txns[0].id == "CARD-001"
