"""Tests for the asyncpg message store."""

from hive.bus.store import MessageStore


async def test_log_and_retrieve_message(store: MessageStore) -> None:
    msg_id = await store.log_message("user", "maestro:dev", "hello")
    assert msg_id >= 1

    messages = await store.get_messages("maestro:dev")
    assert len(messages) == 1
    assert messages[0]["sender"] == "user"
    assert messages[0]["content"] == "hello"
    assert messages[0]["status"] == "pending"


async def test_multiple_messages(store: MessageStore) -> None:
    await store.log_message("user", "maestro:dev", "msg1")
    await store.log_message("user", "maestro:dev", "msg2")
    await store.log_message("user", "maestro:pa", "msg3")

    dev_msgs = await store.get_messages("maestro:dev")
    assert len(dev_msgs) == 2

    pa_msgs = await store.get_messages("maestro:pa")
    assert len(pa_msgs) == 1


async def test_update_status(store: MessageStore) -> None:
    msg_id = await store.log_message("user", "maestro:dev", "hello")
    await store.update_status(msg_id, "delivered")

    messages = await store.get_messages("maestro:dev", status="delivered")
    assert len(messages) == 1
    assert messages[0]["status"] == "delivered"

    pending = await store.get_messages("maestro:dev", status="pending")
    assert len(pending) == 0


async def test_conversation_grouping(store: MessageStore) -> None:
    conv_id = "conv-123"
    await store.log_message("user", "maestro:dev", "q1", conversation_id=conv_id)
    await store.log_message("maestro:dev", "user", "a1", conversation_id=conv_id)
    await store.log_message("user", "maestro:dev", "q2", conversation_id=conv_id)

    conv = await store.get_conversation(conv_id)
    assert len(conv) == 3
    assert conv[0]["content"] == "q1"
    assert conv[1]["content"] == "a1"


async def test_get_recent(store: MessageStore) -> None:
    for i in range(5):
        await store.log_message("user", "maestro:dev", f"msg{i}")

    recent = await store.get_recent(limit=3)
    assert len(recent) == 3
    # Most recent first
    assert recent[0]["content"] == "msg4"


async def test_count_messages(store: MessageStore) -> None:
    await store.log_message("user", "maestro:dev", "a")
    await store.log_message("user", "maestro:pa", "b")

    assert await store.count_messages() == 2
    assert await store.count_messages("maestro:dev") == 1


async def test_filter_by_since(store: MessageStore) -> None:
    import asyncio
    from datetime import UTC, datetime

    await store.log_message("user", "maestro:dev", "old")
    # small gap so TIMESTAMPTZ resolution doesn't collapse the two rows
    await asyncio.sleep(0.01)
    cutoff = datetime.now(UTC)
    await asyncio.sleep(0.01)
    await store.log_message("user", "maestro:dev", "new")

    messages = await store.get_messages("maestro:dev", since=cutoff)
    assert len(messages) == 1
    assert messages[0]["content"] == "new"
