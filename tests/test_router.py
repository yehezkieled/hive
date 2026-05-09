"""Tests for message router."""

from hive.bus.router import MessageRouter


async def test_register_and_route(router: MessageRouter) -> None:
    router.register("maestro:dev")
    await router.route("user", "maestro:dev", "hello")

    msg = await router.get_next("maestro:dev", timeout=1.0)
    assert msg is not None
    assert msg.sender == "user"
    assert msg.content == "hello"


async def test_message_logged_to_store(router: MessageRouter) -> None:
    router.register("maestro:dev")
    await router.route("user", "maestro:dev", "hello")

    # Check it was persisted
    messages = await router.store.get_messages("maestro:dev")
    assert len(messages) == 1


async def test_unregistered_recipient_still_logged(router: MessageRouter) -> None:
    # Don't register anyone — message should still be logged
    await router.route("user", "maestro:dev", "hello")

    messages = await router.store.get_messages("maestro:dev")
    assert len(messages) == 1


async def test_get_next_timeout(router: MessageRouter) -> None:
    router.register("maestro:dev")
    msg = await router.get_next("maestro:dev", timeout=0.1)
    assert msg is None


async def test_has_pending(router: MessageRouter) -> None:
    router.register("maestro:dev")
    assert not router.has_pending("maestro:dev")

    await router.route("user", "maestro:dev", "hello")
    assert router.has_pending("maestro:dev")


async def test_broadcast(router: MessageRouter) -> None:
    router.register("maestro:dev")
    router.register("maestro:pa")

    await router.broadcast("user", "standup time")

    dev_msg = await router.get_next("maestro:dev", timeout=1.0)
    pa_msg = await router.get_next("maestro:pa", timeout=1.0)
    assert dev_msg is not None
    assert pa_msg is not None
    assert dev_msg.content == "standup time"


async def test_broadcast_excludes_sender(router: MessageRouter) -> None:
    router.register("maestro:dev")
    router.register("maestro:pa")

    await router.broadcast("maestro:dev", "I'm done")

    # dev should not get its own broadcast
    assert not router.has_pending("maestro:dev")
    # pa should get it
    assert router.has_pending("maestro:pa")


async def test_registered_entities(router: MessageRouter) -> None:
    router.register("a")
    router.register("b")
    assert set(router.registered_entities) == {"a", "b"}

    router.unregister("a")
    assert router.registered_entities == ["b"]


async def test_wake_callback_fires_on_route(router: MessageRouter) -> None:
    router.register("maestro:dev")
    seen: list[str] = []
    router.wake_callback = seen.append

    await router.route("user", "maestro:dev", "hello")

    assert seen == ["maestro:dev"]


async def test_wake_callback_skipped_for_unregistered_recipient(
    router: MessageRouter,
) -> None:
    seen: list[str] = []
    router.wake_callback = seen.append

    await router.route("user", "maestro:dev", "hello")

    assert seen == []
