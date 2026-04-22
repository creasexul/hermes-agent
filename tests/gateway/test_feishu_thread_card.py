"""Tests for the Feishu in-thread reply card builder.

Covers ``_build_thread_reply_card`` only — no network, no Feishu connection.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Repo root must be importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Stub lark_oapi / aiohttp so the feishu module imports without those deps
# (matches the convention used by tests/gateway/test_feishu_approval_buttons.py)
# ---------------------------------------------------------------------------
def _ensure_feishu_mocks() -> None:
    if "lark_oapi" not in sys.modules:
        mod = MagicMock()
        for name in (
            "lark_oapi",
            "lark_oapi.api.im.v1",
            "lark_oapi.event",
            "lark_oapi.event.callback_type",
        ):
            sys.modules.setdefault(name, mod)
    if "aiohttp" not in sys.modules:
        aio = MagicMock()
        sys.modules.setdefault("aiohttp", aio)
        sys.modules.setdefault("aiohttp.web", aio.web)


_ensure_feishu_mocks()

from gateway.config import PlatformConfig  # noqa: E402
from gateway.platforms.feishu import FeishuAdapter, _build_thread_reply_card  # noqa: E402


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_basic_card_structure() -> None:
    """Plain text-only card has the right schema, header, and one markdown body."""
    card = _build_thread_reply_card("hello")

    assert card["schema"] == "2.0"
    assert card["header"]["title"]["tag"] == "plain_text"
    assert card["header"]["title"]["content"] == "Hermes"
    assert card["header"]["template"] == "blue"

    body_elements = card["body"]["elements"]
    assert isinstance(body_elements, list)
    assert body_elements[0] == {"tag": "markdown", "content": "hello"}

    # No optional sections → no extra elements past the main markdown.
    assert len(body_elements) == 1


def test_update_multi_enabled_for_future_patch() -> None:
    """``config.update_multi`` must be True so PATCH-style updates work later."""
    card = _build_thread_reply_card("hello")
    config = card["config"]
    assert config["update_multi"] is True
    assert config["wide_screen_mode"] is True
    assert config["streaming_mode"] is False


def test_tool_steps_render_collapsible_panel_with_divs() -> None:
    """``tool_steps`` becomes a folded ``collapsible_panel`` with one div per step."""
    card = _build_thread_reply_card(
        "hello",
        tool_steps=[
            {"tool": "bash", "label": "ls /"},
            {"tool": "read", "label": "a.py"},
        ],
    )
    elements = card["body"]["elements"]

    # main markdown + hr + collapsible_panel
    assert elements[0]["tag"] == "markdown"
    assert {"tag": "hr"} in elements

    panels = [e for e in elements if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1
    panel = panels[0]
    assert panel["expanded"] is False
    assert "执行记录" in panel["header"]["title"]["content"]
    assert "2 steps" in panel["header"]["title"]["content"]

    divs = [e for e in panel["elements"] if e.get("tag") == "div"]
    assert len(divs) == 2
    assert divs[0]["text"]["content"] == "ls /"
    assert divs[1]["text"]["content"] == "a.py"
    # Tool icon style matches what Mavis uses
    assert divs[0]["icon"]["token"] == "tools_outlined"


def test_thinking_renders_expanded_collapsible_panel() -> None:
    """``thinking`` becomes an expanded ``collapsible_panel`` containing markdown."""
    card = _build_thread_reply_card("hello", thinking="思考过程")
    elements = card["body"]["elements"]

    panels = [e for e in elements if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1
    panel = panels[0]
    assert panel["expanded"] is True
    assert panel["header"]["title"]["content"] == "Thinking"
    inner = panel["elements"]
    assert inner == [{"tag": "markdown", "content": "思考过程"}]


def test_both_thinking_and_tool_steps_present() -> None:
    """When both are supplied, both panels appear, each with its own hr separator."""
    card = _build_thread_reply_card(
        "hello",
        thinking="t",
        tool_steps=[{"tool": "bash", "label": "x"}],
    )
    elements = card["body"]["elements"]

    panels = [e for e in elements if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 2
    # Two hr separators (one before thinking, one before tool_steps)
    assert sum(1 for e in elements if e.get("tag") == "hr") == 2

    expanded_states = {p["header"]["title"]["content"]: p["expanded"] for p in panels}
    assert expanded_states["Thinking"] is True
    assert expanded_states["执行记录 · 1 step"] is False


def test_custom_bot_name_propagates_to_header() -> None:
    card = _build_thread_reply_card("hi", bot_name="Mavis")
    assert card["header"]["title"]["content"] == "Mavis"


# ---------------------------------------------------------------------------
# append_tool_step + edit interactions on a live FeishuAdapter instance
# ---------------------------------------------------------------------------


def _make_adapter() -> FeishuAdapter:
    config = PlatformConfig(enabled=True)
    adapter = FeishuAdapter(config)
    adapter._client = MagicMock()
    return adapter


def _register_card(adapter: FeishuAdapter, message_id: str, main_text: str = "hello") -> None:
    """Pretend ``send()`` already shipped a thread-reply card with this id."""
    adapter._card_messages[message_id] = {
        "chat_id": "oc_chat",
        "reply_to": "om_anchor",
        "metadata": {"thread_id": "omt_topic"},
    }
    adapter._card_state[message_id] = {
        "main_text": main_text,
        "tool_steps": [],
    }


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.code = 0
    resp.msg = "ok"
    return resp


def _captured_card(patch_mock: AsyncMock) -> dict:
    """Pull the card dict out of the most recent _patch_card_message call."""
    args, kwargs = patch_mock.call_args
    if "card" in kwargs:
        return kwargs["card"]
    # Positional: (message_id, card)
    return args[1]


def test_append_tool_step_patches_card_with_collapsible_panel() -> None:
    """A tracked card gets PATCH'd with a folded ``执行记录`` panel + step div."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_1", main_text="answer body")
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    result = asyncio.run(
        adapter.append_tool_step(
            "om_card_1",
            {"tool": "search_files", "label": "🔎 search_files: \"foo\""},
        )
    )

    assert result.success is True
    assert result.message_id == "om_card_1"

    adapter._patch_card_message.assert_awaited_once()
    card = _captured_card(adapter._patch_card_message)
    panels = [e for e in card["body"]["elements"] if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1
    panel = panels[0]
    assert panel["expanded"] is False
    assert "执行记录" in panel["header"]["title"]["content"]
    divs = [e for e in panel["elements"] if e.get("tag") == "div"]
    assert len(divs) == 1
    assert "search_files" in divs[0]["text"]["content"]

    # State got accumulated for the next call
    assert len(adapter._card_state["om_card_1"]["tool_steps"]) == 1


def test_append_tool_step_unknown_message_id_is_noop() -> None:
    """No registered state → no patch attempt, success returned for safety."""
    adapter = _make_adapter()
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    result = asyncio.run(
        adapter.append_tool_step("om_unknown", {"tool": "x", "label": "y"})
    )

    assert result.success is True
    adapter._patch_card_message.assert_not_called()


def test_append_tool_step_swallows_patch_failure_without_raising() -> None:
    """A patch exception must not propagate to the progress loop."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_2")
    adapter._patch_card_message = AsyncMock(side_effect=RuntimeError("boom"))

    result = asyncio.run(
        adapter.append_tool_step("om_card_2", {"tool": "t", "label": "L"})
    )

    assert result.success is False
    assert "boom" in (result.error or "")
    # State is still updated so the next edit/append can replay it
    assert adapter._card_state["om_card_2"]["tool_steps"] == [{"tool": "t", "label": "L"}]


def test_edit_thread_reply_card_preserves_accumulated_tool_steps() -> None:
    """A streaming edit (after two appended tool steps) re-renders the panel."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_3", main_text="initial")

    patches: list = []

    async def _record_patch(message_id, card):
        patches.append((message_id, json.loads(json.dumps(card))))
        return _ok_response()

    adapter._patch_card_message = AsyncMock(side_effect=_record_patch)

    asyncio.run(
        adapter.append_tool_step("om_card_3", {"tool": "a", "label": "step-a"})
    )
    asyncio.run(
        adapter.append_tool_step("om_card_3", {"tool": "b", "label": "step-b"})
    )
    asyncio.run(
        adapter._edit_thread_reply_card(
            stale_message_id="om_card_3",
            card_ctx=adapter._card_messages["om_card_3"],
            fallback_chat_id="oc_chat",
            content="updated body",
        )
    )

    # Three patches: 2 appends + 1 streaming edit
    assert len(patches) == 3
    last_msg_id, last_card = patches[-1]
    assert last_msg_id == "om_card_3"

    # Main markdown updated
    assert last_card["body"]["elements"][0]["content"] == "updated body"

    panels = [
        e for e in last_card["body"]["elements"] if e.get("tag") == "collapsible_panel"
    ]
    assert len(panels) == 1
    divs = [e for e in panels[0]["elements"] if e.get("tag") == "div"]
    labels = [d["text"]["content"] for d in divs]
    assert labels == ["step-a", "step-b"]


def test_append_tool_step_no_client_records_state_only() -> None:
    """Without a live ``_client`` we still bookkeep the step (best-effort)."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_4")
    adapter._client = None
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    result = asyncio.run(
        adapter.append_tool_step("om_card_4", {"tool": "t", "label": "L"})
    )

    assert result.success is True
    adapter._patch_card_message.assert_not_called()
    assert adapter._card_state["om_card_4"]["tool_steps"] == [{"tool": "t", "label": "L"}]


# ---------------------------------------------------------------------------
# Placeholder card flow: pre-send "…" so tool steps + final response land on
# the same card surface (covers the gateway/run.py wiring at the adapter
# boundary — gateway integration itself isn't unit-tested here).
# ---------------------------------------------------------------------------


def _ok_send_response(message_id: str) -> MagicMock:
    """Mock a successful Feishu send response carrying ``message_id``."""
    resp = MagicMock()
    resp.success = lambda: True
    resp.code = 0
    resp.msg = "ok"
    data = MagicMock()
    data.message_id = message_id
    resp.data = data
    return resp


def test_send_placeholder_card_initializes_card_state() -> None:
    """A successful thread-reply card send registers ``_card_state`` so later
    ``append_tool_step`` and ``edit_message`` calls can find it."""
    adapter = _make_adapter()
    adapter._feishu_send_with_retry = AsyncMock(
        return_value=_ok_send_response("om_placeholder_1")
    )

    result = asyncio.run(
        adapter.send(
            chat_id="oc_chat",
            content="…",
            reply_to="om_anchor",
            metadata={"thread_id": "omt_topic"},
        )
    )

    assert result.success is True
    assert result.message_id == "om_placeholder_1"

    # _card_messages tracks send-time context (chat_id, reply_to, metadata)
    ctx = adapter._card_messages["om_placeholder_1"]
    assert ctx["chat_id"] == "oc_chat"
    assert ctx["reply_to"] == "om_anchor"
    assert ctx["metadata"] == {"thread_id": "omt_topic"}

    # _card_state ready for tool_steps accumulation
    state = adapter._card_state["om_placeholder_1"]
    assert state["tool_steps"] == []
    assert state["main_text"] == "…"

    # The send went through the interactive (card) path, not the text path
    call = adapter._feishu_send_with_retry.await_args
    assert call.kwargs.get("msg_type") == "interactive"


def test_edit_message_after_placeholder_uses_card_path() -> None:
    """``edit_message`` on a tracked placeholder PATCHes the card, not the
    text/post update API."""
    adapter = _make_adapter()
    _register_card(adapter, "om_placeholder_2", main_text="…")
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())
    # If the test path leaks into the normal text/post update API, this mock
    # would be called — assert at the end that it wasn't.
    adapter._client.im.v1.message.update = MagicMock()

    result = asyncio.run(
        adapter.edit_message(
            chat_id="oc_chat",
            message_id="om_placeholder_2",
            content="final answer",
        )
    )

    assert result.success is True
    assert result.message_id == "om_placeholder_2"
    adapter._patch_card_message.assert_awaited_once()
    adapter._client.im.v1.message.update.assert_not_called()

    # Card body's main markdown reflects the edited content
    card = _captured_card(adapter._patch_card_message)
    assert card["body"]["elements"][0]["content"] == "final answer"
    # main_text in state was updated by the edit so future patches re-render it
    assert adapter._card_state["om_placeholder_2"]["main_text"] == "final answer"


def test_append_tool_step_then_edit_message_preserves_steps() -> None:
    """End-to-end via the public adapter surface: register placeholder →
    append two steps → edit_message with final text → final card has the
    new main markdown AND both tool steps in the collapsible panel."""
    adapter = _make_adapter()
    _register_card(adapter, "om_placeholder_3", main_text="…")

    patches: list = []

    async def _record_patch(message_id, card):
        patches.append((message_id, json.loads(json.dumps(card))))
        return _ok_response()

    adapter._patch_card_message = AsyncMock(side_effect=_record_patch)

    asyncio.run(
        adapter.append_tool_step(
            "om_placeholder_3", {"tool": "bash", "label": "ls /"},
        )
    )
    asyncio.run(
        adapter.append_tool_step(
            "om_placeholder_3", {"tool": "read", "label": "a.py"},
        )
    )
    final = asyncio.run(
        adapter.edit_message(
            chat_id="oc_chat",
            message_id="om_placeholder_3",
            content="here is the answer",
        )
    )

    assert final.success is True
    # 2 appends + 1 final edit = 3 patches
    assert len(patches) == 3
    last_id, last_card = patches[-1]
    assert last_id == "om_placeholder_3"
    assert last_card["body"]["elements"][0]["content"] == "here is the answer"

    panels = [
        e for e in last_card["body"]["elements"]
        if e.get("tag") == "collapsible_panel"
    ]
    assert len(panels) == 1
    labels = [d["text"]["content"] for d in panels[0]["elements"]
              if d.get("tag") == "div"]
    assert labels == ["ls /", "a.py"]


# ---------------------------------------------------------------------------
# GatewayStreamConsumer.adopt_message_id: covers the streaming-on path
# where the consumer must inherit the placeholder card id so its first
# edit lands on the same surface.
# ---------------------------------------------------------------------------


def test_stream_consumer_adopt_message_id_targets_placeholder() -> None:
    """``adopt_message_id`` makes the consumer treat the given id as the
    current edit target and mark it as already sent."""
    from gateway.stream_consumer import GatewayStreamConsumer

    consumer = GatewayStreamConsumer(adapter=MagicMock(), chat_id="oc_chat")
    assert consumer._message_id is None
    assert consumer.already_sent is False

    consumer.adopt_message_id("om_placeholder_4")

    assert consumer._message_id == "om_placeholder_4"
    assert consumer.already_sent is True


def test_stream_consumer_adopt_message_id_ignores_empty() -> None:
    """Empty/None message_id is a no-op (placeholder send may have failed)."""
    from gateway.stream_consumer import GatewayStreamConsumer

    consumer = GatewayStreamConsumer(adapter=MagicMock(), chat_id="oc_chat")
    consumer.adopt_message_id("")
    assert consumer._message_id is None
    assert consumer.already_sent is False


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
