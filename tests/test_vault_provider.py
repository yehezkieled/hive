"""Sprint 25 — payment provider tests."""

from __future__ import annotations

import pytest

from hive.vault.provider import (
    ExecutionResult,
    PaymentProvider,
    StubPaymentProvider,
    build_provider,
)


async def test_stub_provider_success() -> None:
    provider = StubPaymentProvider()
    result = await provider.execute(
        {
            "id": 7,
            "amount_cents": 250,
            "currency": "USD",
            "recipient": "x@y.z",
            "description": "regular payment",
            "idempotency_key": "smoke-1",
            "payload": {"reason": "donation"},
        }
    )
    assert result.ok is True
    assert result.provider == "stub"
    assert result.reference == "stub-7-smoke-1"
    payload = result.to_payload()
    assert payload["ok"] is True
    assert payload["reference"] == "stub-7-smoke-1"


async def test_stub_provider_force_fail_marker_in_description() -> None:
    provider = StubPaymentProvider()
    result = await provider.execute(
        {
            "id": 8,
            "amount_cents": 100,
            "currency": "USD",
            "description": "Pay 1.00 USD: FORCE_FAIL diagnostic",
            "idempotency_key": "ff-1",
            "payload": {},
        }
    )
    assert result.ok is False
    assert result.error is not None
    assert "FORCE_FAIL" in result.error


async def test_stub_provider_force_fail_marker_in_payload_reason() -> None:
    provider = StubPaymentProvider()
    result = await provider.execute(
        {
            "id": 9,
            "amount_cents": 100,
            "currency": "USD",
            "description": "vanilla description",
            "idempotency_key": "ff-2",
            "payload": {"reason": "FORCE_FAIL via payload"},
        }
    )
    assert result.ok is False


def test_build_provider_returns_stub_for_known_name() -> None:
    provider = build_provider("stub")
    assert isinstance(provider, StubPaymentProvider)


def test_build_provider_falls_back_to_stub_for_unknown_name() -> None:
    provider = build_provider("definitely-not-a-real-provider")
    assert isinstance(provider, StubPaymentProvider)


def test_stub_provider_satisfies_payment_provider_protocol() -> None:
    provider = StubPaymentProvider()
    assert isinstance(provider, PaymentProvider)


def test_execution_result_to_payload_omits_none_fields() -> None:
    result = ExecutionResult(ok=True, provider="stub", reference="ref-1")
    payload = result.to_payload()
    assert payload == {"provider": "stub", "ok": True, "reference": "ref-1"}
    assert "error" not in payload
    assert "details" not in payload
