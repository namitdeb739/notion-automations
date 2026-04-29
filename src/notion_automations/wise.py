"""Wise Platform API client for personal finance data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx

_BASE = "https://api.wise.com"


@dataclass
class WiseTransaction:
    id: str
    date: datetime
    amount: Decimal  # always positive, in SGD
    direction: str  # "Debit" or "Credit" — matches Notion select values
    merchant: str | None
    reference: str | None
    transaction_type: str  # e.g. CARD, TRANSFER, CONVERSION, FEE
    original_amount: Decimal | None  # pre-conversion amount; None for SGD transactions
    original_currency: str | None  # e.g. "GBP"; None for SGD transactions
    exchange_rate: Decimal | None  # SGD per original unit; None for SGD transactions


def _parse_txn(raw: dict[str, Any]) -> WiseTransaction:
    wise_type = raw.get("type", "DEBIT")  # DEBIT | CREDIT | NEUTRAL
    direction = "Credit" if wise_type == "CREDIT" else "Debit"

    amount_val = Decimal(str(raw["amount"]["value"]))
    amount = abs(amount_val)

    details: dict[str, Any] = raw.get("details") or {}
    merchant_obj: dict[str, Any] = details.get("merchant") or {}
    explicit_merchant: str | None = merchant_obj.get("name") or None
    reference: str | None = details.get("description") or None
    txn_type = details.get("type") or ""
    # Cards: use merchant name, or fall back to description when absent.
    # Non-card (transfers, fees): merchant is None; reference holds the description.
    if explicit_merchant:
        merchant: str | None = explicit_merchant
    elif txn_type == "CARD" and reference:
        merchant = reference
    else:
        merchant = None

    # Original (pre-conversion) amount — present when a foreign currency card was used.
    orig_amount_obj: dict[str, Any] = details.get("amount") or {}
    orig_currency: str | None = orig_amount_obj.get("currency") or None
    original_amount: Decimal | None = None
    original_currency: str | None = None
    exchange_rate: Decimal | None = None
    if orig_currency and orig_currency != "SGD":
        original_amount = abs(Decimal(str(orig_amount_obj["value"])))
        original_currency = orig_currency
        exchange_obj: dict[str, Any] = raw.get("exchangeDetails") or {}
        rate = exchange_obj.get("rate")
        if rate is not None:
            exchange_rate = Decimal(str(rate))

    date_str: str = raw["date"]
    date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))

    return WiseTransaction(
        id=raw["referenceNumber"],
        date=date,
        amount=amount,
        direction=direction,
        merchant=merchant,
        reference=reference,
        transaction_type=details.get("type") or "",
        original_amount=original_amount,
        original_currency=original_currency,
        exchange_rate=exchange_rate,
    )


class WiseClient:
    def __init__(self, token: str, _http: httpx.Client | None = None) -> None:
        self._client = _http or httpx.Client(
            base_url=_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30.0,
        )

    def get_personal_profile_id(self) -> int:
        resp = self._client.get("/v1/profiles")
        resp.raise_for_status()
        profiles: list[dict[str, Any]] = resp.json()
        for p in profiles:
            if str(p.get("type", "")).lower() == "personal":
                return int(p["id"])
        raise RuntimeError("No personal profile found in Wise account.")

    def get_balances(self, profile_id: int) -> list[dict[str, Any]]:
        resp = self._client.get(
            f"/v4/profiles/{profile_id}/balances",
            params={"types": "STANDARD"},
        )
        resp.raise_for_status()
        return list(resp.json())

    def get_sgd_balance_id(self, profile_id: int) -> int:
        for b in self.get_balances(profile_id):
            if b.get("currency") == "SGD":
                return int(b["id"])
        raise RuntimeError("No SGD balance found in Wise account.")

    def get_statement(
        self,
        profile_id: int,
        balance_id: int,
        since: datetime,
        until: datetime,
    ) -> list[WiseTransaction]:
        resp = self._client.get(
            f"/v1/profiles/{profile_id}/balance-statements/{balance_id}/statement.json",
            params={
                "currency": "SGD",
                "intervalStart": since.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "intervalEnd": until.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                "type": "COMPACT",
            },
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        return [_parse_txn(t) for t in data.get("transactions", [])]

    def get_all_transactions(
        self,
        profile_id: int,
        since: datetime,
        until: datetime,
    ) -> list[WiseTransaction]:
        balance_id = self.get_sgd_balance_id(profile_id)
        return self.get_statement(profile_id, balance_id, since, until)
