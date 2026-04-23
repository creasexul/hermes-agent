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
    # Tool calls (bash, read, etc.) use gear icon
    assert divs[0]["icon"]["token"] == "setting-2_outlined"
    assert divs[1]["icon"]["token"] == "setting-2_outlined"


def test_tool_step_icon_splits_by_tool_type() -> None:
    """Text/thinking steps use chat_outlined; tool calls use setting-2_outlined."""
    from gateway.platforms.feishu import _tool_step_to_div

    tool_div = _tool_step_to_div({"tool": "bash", "label": "run"})
    assert tool_div["icon"]["token"] == "setting-2_outlined"

    for text_tool in ("text", "message", "thinking", "reasoning", ""):
        div = _tool_step_to_div({"tool": text_tool, "label": "msg"})
        assert div["icon"]["token"] == "chat_outlined", f"failed for tool={text_tool!r}"


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


# ---------------------------------------------------------------------------
# One-card-per-turn: send() routes subsequent calls to _update_main_text
# ---------------------------------------------------------------------------


def test_send_second_call_for_same_thread_patches_primary_card() -> None:
    """When a live card already exists for (chat_id, thread_id), a second
    send() patches it via _update_main_text instead of creating a new card.

    The (chat_id, thread_id) -> message_id mapping now lives in the
    per-call ``_PRIMARY_CARD_HOLDER`` ContextVar (set by gateway.run for
    every ``_run_agent`` invocation), so the test seeds the holder
    explicitly before invoking ``send()``.
    """
    from gateway.platforms.feishu import _PRIMARY_CARD_HOLDER

    adapter = _make_adapter()
    # Manually register a primary card (simulating the placeholder that was
    # pre-sent by run.py before streaming started).
    _register_card(adapter, "om_primary", main_text="…")
    _holder = {("oc_chat", "omt_topic"): "om_primary"}
    _token = _PRIMARY_CARD_HOLDER.set(_holder)
    try:
        adapter._patch_card_message = AsyncMock(return_value=_ok_response())

        result = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="streaming update",
                reply_to="om_anchor",
                metadata={"thread_id": "omt_topic"},
            )
        )

        # Must return the SAME primary card id — NOT a new one
        assert result.success is True
        assert result.message_id == "om_primary"

        # _update_main_text must have patched the card (no new send to Feishu)
        adapter._patch_card_message.assert_awaited_once()
        card = _captured_card(adapter._patch_card_message)
        assert card["body"]["elements"][0]["content"] == "streaming update"

        # State was updated too
        assert adapter._card_state["om_primary"]["main_text"] == "streaming update"
    finally:
        _PRIMARY_CARD_HOLDER.reset(_token)


def test_send_first_call_registers_primary_card() -> None:
    """The very first send() for a (chat_id, thread_id) creates a new card
    AND registers it in the per-call ContextVar holder so subsequent calls
    in the same turn route to patch."""
    from gateway.platforms.feishu import _PRIMARY_CARD_HOLDER

    adapter = _make_adapter()
    adapter._feishu_send_with_retry = AsyncMock(
        return_value=_ok_send_response("om_new_card")
    )
    _holder: dict = {}
    _token = _PRIMARY_CARD_HOLDER.set(_holder)
    try:
        asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="hello",
                reply_to="om_anchor",
                metadata={"thread_id": "omt_topic"},
            )
        )
    finally:
        _PRIMARY_CARD_HOLDER.reset(_token)

    # The send registered the new card in THIS call's holder.
    assert _holder.get(("oc_chat", "omt_topic")) == "om_new_card"


def test_update_main_text_patches_with_current_tool_steps() -> None:
    """``_update_main_text`` rebuilds the card including accumulated tool_steps."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_5", main_text="old text")
    # Pre-populate tool steps
    adapter._card_state["om_card_5"]["tool_steps"] = [
        {"tool": "bash", "label": "step-a"},
        {"tool": "read", "label": "step-b"},
    ]
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    result = asyncio.run(
        adapter._update_main_text("om_card_5", "new text")
    )

    assert result.success is True
    assert result.message_id == "om_card_5"

    adapter._patch_card_message.assert_awaited_once()
    card = _captured_card(adapter._patch_card_message)
    # main_text updated
    assert card["body"]["elements"][0]["content"] == "new text"
    # tool_steps preserved in collapsible panel
    panels = [e for e in card["body"]["elements"] if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1
    labels = [d["text"]["content"] for d in panels[0]["elements"] if d.get("tag") == "div"]
    assert labels == ["step-a", "step-b"]


def test_update_main_text_returns_success_even_on_patch_failure() -> None:
    """Patch failures are non-fatal: _update_main_text still returns success=True
    so the stream consumer does not enter fallback mode."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_6", main_text="old")
    adapter._patch_card_message = AsyncMock(side_effect=RuntimeError("network error"))

    result = asyncio.run(adapter._update_main_text("om_card_6", "new"))

    # Always success so the consumer stays anchored to this card
    assert result.success is True
    assert result.message_id == "om_card_6"
    # State still updated (next patch will re-apply)
    assert adapter._card_state["om_card_6"]["main_text"] == "new"


# ---------------------------------------------------------------------------
# Per-call holder lifetime: cross-query / cross-thread isolation
# ---------------------------------------------------------------------------


def test_two_queries_in_same_thread_create_two_independent_cards() -> None:
    """Two consecutive turns in the same (chat_id, thread_id) — each with its
    own per-call holder, mimicking what gateway.run._run_agent does — must
    create TWO different cards.  Regression guard: the previous adapter-level
    ``self._primary_card`` dict caused turn 2 to patch turn 1's card."""
    from gateway.platforms.feishu import _PRIMARY_CARD_HOLDER

    adapter = _make_adapter()
    # Each call returns a fresh message_id so we can tell the cards apart.
    adapter._feishu_send_with_retry = AsyncMock(
        side_effect=[
            _ok_send_response("om_turn_1"),
            _ok_send_response("om_turn_2"),
        ]
    )
    # Patch path must NOT be invoked — every turn ships a fresh card.
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    # ---- Turn 1: fresh per-call holder ----
    _holder_1: dict = {}
    _token_1 = _PRIMARY_CARD_HOLDER.set(_holder_1)
    try:
        r1 = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="first query",
                reply_to="om_anchor_1",
                metadata={"thread_id": "omt_topic"},
            )
        )
    finally:
        _PRIMARY_CARD_HOLDER.reset(_token_1)

    # ---- Turn 2: fresh per-call holder (key insight: NEW dict, not the
    # turn-1 one) ----
    _holder_2: dict = {}
    _token_2 = _PRIMARY_CARD_HOLDER.set(_holder_2)
    try:
        r2 = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="second query",
                reply_to="om_anchor_2",
                metadata={"thread_id": "omt_topic"},
            )
        )
    finally:
        _PRIMARY_CARD_HOLDER.reset(_token_2)

    # Two different cards
    assert r1.success is True and r2.success is True
    assert r1.message_id == "om_turn_1"
    assert r2.message_id == "om_turn_2"
    assert r1.message_id != r2.message_id

    # Each holder ended up with its own card id (no cross-contamination)
    assert _holder_1.get(("oc_chat", "omt_topic")) == "om_turn_1"
    assert _holder_2.get(("oc_chat", "omt_topic")) == "om_turn_2"

    # Critical: turn 2 did NOT patch turn 1's card
    adapter._patch_card_message.assert_not_called()
    # Both turns went through the create-card path
    assert adapter._feishu_send_with_retry.await_count == 2


def test_two_sends_in_same_query_patch_the_same_card() -> None:
    """Within a single turn (= single per-call holder), a follow-up send()
    after the initial card is shipped must PATCH the existing card rather
    than creating a new one.  Mirrors the stream-consumer flow where a
    segment break resets ``_message_id=None`` and the consumer calls
    ``send()`` again on the same surface."""
    from gateway.platforms.feishu import _PRIMARY_CARD_HOLDER

    adapter = _make_adapter()
    # Only the FIRST send must call the create API; the second goes through
    # _update_main_text -> _patch_card_message.
    adapter._feishu_send_with_retry = AsyncMock(
        return_value=_ok_send_response("om_only_card")
    )
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    _holder: dict = {}
    _token = _PRIMARY_CARD_HOLDER.set(_holder)
    try:
        r1 = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="first chunk",
                reply_to="om_anchor",
                metadata={"thread_id": "omt_topic"},
            )
        )
        r2 = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="second chunk",
                reply_to="om_anchor",
                metadata={"thread_id": "omt_topic"},
            )
        )
    finally:
        _PRIMARY_CARD_HOLDER.reset(_token)

    # Both sends report the same message id (the original card)
    assert r1.message_id == "om_only_card"
    assert r2.message_id == "om_only_card"

    # Only ONE create-card API call (the first); the second was a patch
    assert adapter._feishu_send_with_retry.await_count == 1
    adapter._patch_card_message.assert_awaited_once()
    card = _captured_card(adapter._patch_card_message)
    assert card["body"]["elements"][0]["content"] == "second chunk"
    # State reflects the latest content
    assert adapter._card_state["om_only_card"]["main_text"] == "second chunk"


def test_same_chat_different_thread_uses_different_cards() -> None:
    """Even within a single per-call holder, two different thread_ids in the
    same chat get their own cards — the holder is keyed by
    (chat_id, thread_id).  Guards against accidental sharing across threads."""
    from gateway.platforms.feishu import _PRIMARY_CARD_HOLDER

    adapter = _make_adapter()
    adapter._feishu_send_with_retry = AsyncMock(
        side_effect=[
            _ok_send_response("om_thread_a"),
            _ok_send_response("om_thread_b"),
        ]
    )
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    _holder: dict = {}
    _token = _PRIMARY_CARD_HOLDER.set(_holder)
    try:
        r_a = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="hi from thread A",
                reply_to="om_anchor_a",
                metadata={"thread_id": "omt_thread_a"},
            )
        )
        r_b = asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="hi from thread B",
                reply_to="om_anchor_b",
                metadata={"thread_id": "omt_thread_b"},
            )
        )
    finally:
        _PRIMARY_CARD_HOLDER.reset(_token)

    # Two different cards (different thread ids = different holder keys)
    assert r_a.message_id == "om_thread_a"
    assert r_b.message_id == "om_thread_b"
    assert _holder == {
        ("oc_chat", "omt_thread_a"): "om_thread_a",
        ("oc_chat", "omt_thread_b"): "om_thread_b",
    }

    # Both turns went through the create-card path; no cross-thread patch
    assert adapter._feishu_send_with_retry.await_count == 2
    adapter._patch_card_message.assert_not_called()


def test_send_outside_of_run_agent_creates_fresh_card_each_time() -> None:
    """When ``send()`` is called without any per-call holder (e.g. cron
    delivery, CLI tools, ad-hoc notifications), the holder ContextVar's
    default of ``None`` makes the lookup short-circuit and every call ships
    a new card.  Guards the semantic that the holder is ONLY active inside
    ``_run_agent``."""
    from gateway.platforms.feishu import _PRIMARY_CARD_HOLDER

    # Sanity-check: with no token set, the default is None.
    assert _PRIMARY_CARD_HOLDER.get() is None

    adapter = _make_adapter()
    adapter._feishu_send_with_retry = AsyncMock(
        side_effect=[
            _ok_send_response("om_one"),
            _ok_send_response("om_two"),
        ]
    )
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    r1 = asyncio.run(
        adapter.send(
            chat_id="oc_chat",
            content="first ad-hoc",
            reply_to="om_anchor",
            metadata={"thread_id": "omt_topic"},
        )
    )
    r2 = asyncio.run(
        adapter.send(
            chat_id="oc_chat",
            content="second ad-hoc",
            reply_to="om_anchor",
            metadata={"thread_id": "omt_topic"},
        )
    )

    # Both calls created their own card; no patching happened
    assert r1.message_id == "om_one"
    assert r2.message_id == "om_two"
    assert adapter._feishu_send_with_retry.await_count == 2
    adapter._patch_card_message.assert_not_called()
