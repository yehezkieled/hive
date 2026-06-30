"""Tests for PushSubscriptionStore (Ticket 041 web-push subscription persistence)."""

from __future__ import annotations

import pytest_asyncio

from hive.bus.push_subscription_store import PushSubscriptionStore


@pytest_asyncio.fixture
async def push_store(store):
    await store.pool.execute("TRUNCATE push_subscriptions")
    return PushSubscriptionStore(store.pool)


async def test_upsert_new_sub_appears_in_all(push_store):
    sub = {
        "endpoint": "https://push.example/abc",
        "p256dh": "key-p256dh",
        "auth": "key-auth",
        "user_agent": "iPad Safari",
    }
    await push_store.upsert(sub)

    rows = await push_store.all()
    assert len(rows) == 1
    row = rows[0]
    assert row["endpoint"] == "https://push.example/abc"
    assert row["p256dh"] == "key-p256dh"
    assert row["auth"] == "key-auth"
    assert row["user_agent"] == "iPad Safari"


async def test_upsert_same_endpoint_is_idempotent(push_store):
    endpoint = "https://push.example/abc"
    await push_store.upsert(
        {
            "endpoint": endpoint,
            "p256dh": "old-p256dh",
            "auth": "old-auth",
            "user_agent": "iPad Safari",
        }
    )
    await push_store.upsert(
        {
            "endpoint": endpoint,
            "p256dh": "new-p256dh",
            "auth": "new-auth",
            "user_agent": "iPad Safari",
        }
    )

    rows = await push_store.all()
    assert len(rows) == 1
    assert rows[0]["p256dh"] == "new-p256dh"
    assert rows[0]["auth"] == "new-auth"


async def test_delete_removes_the_sub(push_store):
    endpoint = "https://push.example/abc"
    await push_store.upsert(
        {
            "endpoint": endpoint,
            "p256dh": "key-p256dh",
            "auth": "key-auth",
            "user_agent": "iPad Safari",
        }
    )

    await push_store.delete(endpoint)

    assert await push_store.all() == []


async def test_all_on_empty_table_is_empty(push_store):
    assert await push_store.all() == []
