"""Tests for the Runtime abstract interface."""

import pytest

from hive.runtime.base import Runtime


class _ConcreteRuntime(Runtime):
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def is_alive(self) -> bool:
        return True

    async def send_turn(self, prompt: str) -> tuple[str, dict[str, int]]:
        return ("ok", {"input_tokens": 1, "output_tokens": 1})


def test_concrete_subclass_is_instantiable() -> None:
    rt = _ConcreteRuntime()
    assert isinstance(rt, Runtime)


def test_cannot_instantiate_abstract_runtime() -> None:
    with pytest.raises(TypeError):
        Runtime()  # type: ignore[abstract]


async def test_send_turn_returns_text_and_usage_dict() -> None:
    rt = _ConcreteRuntime()
    text, usage = await rt.send_turn("hello")
    assert isinstance(text, str)
    assert "input_tokens" in usage
    assert "output_tokens" in usage
