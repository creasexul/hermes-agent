"""Tests for the Feishu thread-reply card @-mention-sender behaviour.

Verifies the per-call ``_MENTION_TARGET_HOLDER`` ContextVar plus the
``_prepend_feishu_mention`` helper used by ``gateway.run._run_agent`` to
inject ``<at id=open_id></at>`` into the FINAL card body — and only the
final write, not the placeholder, intermediate edits, or tool-step patches.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# Repo root must be importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Stub lark_oapi / aiohttp so the feishu module imports without those deps.
# Mirrors test_feishu_thread_card.py so the two files can run in any order.
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
from gateway.platforms.feishu import (  # noqa: E402
    FeishuAdapter,
    _MENTION_TARGET_HOLDER,
    _format_feishu_at_mention,
    _prepend_feishu_mention,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers (mirror test_feishu_thread_card.py shape)
# ---------------------------------------------------------------------------


def _make_adapter() -> FeishuAdapter:
    config = PlatformConfig(enabled=True)
    adapter = FeishuAdapter(config)
    adapter._client = MagicMock()
    return adapter


def _register_card(
    adapter: FeishuAdapter, message_id: str, main_text: str = "…"
) -> None:
    adapter._card_messages[message_id] = {
        "chat_id": "oc_chat",
        "reply_to": "om_anchor",
        "metadata": {"thread_id": "omt_topic"},
    }
    adapter._card_state[message_id] = {"main_text": main_text, "tool_steps": []}


def _ok_response() -> MagicMock:
    resp = MagicMock()
    resp.code = 0
    resp.msg = "ok"
    return resp


def _captured_card(patch_mock: AsyncMock) -> dict:
    args, kwargs = patch_mock.call_args
    if "card" in kwargs:
        return kwargs["card"]
    return args[1]


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------


def test_format_feishu_at_mention_returns_at_tag() -> None:
    """``_format_feishu_at_mention`` produces the bare ``<at id=...></at>`` syntax."""
    assert _format_feishu_at_mention("ou_alice") == "<at id=ou_alice></at>"


def test_prepend_feishu_mention_with_explicit_open_id() -> None:
    """Explicit open_id wins over the ContextVar (no need to set the holder)."""
    out = _prepend_feishu_mention("hello world", open_id="ou_bob")
    assert out == "<at id=ou_bob></at>\n\nhello world"


def test_prepend_feishu_mention_returns_input_when_no_target() -> None:
    """No explicit open_id and no ContextVar set → text unchanged (cron path)."""
    # Sanity: the default value of the ContextVar is None.
    assert _MENTION_TARGET_HOLDER.get() is None
    assert _prepend_feishu_mention("hello") == "hello"


def test_prepend_feishu_mention_reads_contextvar() -> None:
    """When no open_id is passed, the helper falls back to the ContextVar."""
    token = _MENTION_TARGET_HOLDER.set("ou_carol")
    try:
        assert (
            _prepend_feishu_mention("answer")
            == "<at id=ou_carol></at>\n\nanswer"
        )
    finally:
        _MENTION_TARGET_HOLDER.reset(token)
    # After reset the helper is a no-op again.
    assert _prepend_feishu_mention("answer") == "answer"


def test_prepend_feishu_mention_empty_text_unchanged() -> None:
    """Empty/blank text passes through (helper never returns just an @)."""
    assert _prepend_feishu_mention("", open_id="ou_x") == ""
    assert _prepend_feishu_mention(None, open_id="ou_x") in (None, "")


# ---------------------------------------------------------------------------
# Final-write rendering: the mention lands in a markdown element of the card
# ---------------------------------------------------------------------------


def test_final_edit_with_mention_renders_at_tag_in_markdown_element() -> None:
    """Calling edit_message with a mention-prepended final response produces
    a card whose first body element is a markdown element containing the
    ``<at id=...>`` tag.  The mention syntax requires the ``markdown`` tag —
    plain_text would render the raw HTML — so this test guards both the
    syntax format AND the element tag choice."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_dm", main_text="…")
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    final_text = _prepend_feishu_mention(
        "here is your answer", open_id="ou_sender_dm"
    )
    result = asyncio.run(
        adapter.edit_message(
            chat_id="oc_chat",
            message_id="om_card_dm",
            content=final_text,
        )
    )

    assert result.success is True
    adapter._patch_card_message.assert_awaited_once()

    card = _captured_card(adapter._patch_card_message)
    body = card["body"]["elements"]
    main = body[0]
    assert main["tag"] == "markdown"
    assert main["content"].startswith("<at id=ou_sender_dm></at>")
    # Standard layout: mention, then a blank line, then the answer body
    assert main["content"] == "<at id=ou_sender_dm></at>\n\nhere is your answer"
    # main_text in state is updated to include the @ so future patches
    # (e.g. another tool step append on the same card) preserve the mention.
    assert (
        adapter._card_state["om_card_dm"]["main_text"]
        == "<at id=ou_sender_dm></at>\n\nhere is your answer"
    )


def test_final_edit_with_mention_in_thread_preserves_tool_steps_panel() -> None:
    """Group + thread scenario: after two tool steps were appended to the card,
    the FINAL edit prepends the @-mention into the markdown body AND the
    collapsible 执行记录 panel still contains both step labels (regression
    guard for the per-query card-routing logic)."""
    adapter = _make_adapter()
    _register_card(adapter, "om_card_thread", main_text="…")

    patches: list = []

    async def _record_patch(message_id, card):
        patches.append((message_id, json.loads(json.dumps(card))))
        return _ok_response()

    adapter._patch_card_message = AsyncMock(side_effect=_record_patch)

    asyncio.run(
        adapter.append_tool_step(
            "om_card_thread", {"tool": "bash", "label": "ls /"}
        )
    )
    asyncio.run(
        adapter.append_tool_step(
            "om_card_thread", {"tool": "read", "label": "a.py"}
        )
    )
    final_text = _prepend_feishu_mention(
        "all done — see results above", open_id="ou_thread_sender"
    )
    asyncio.run(
        adapter.edit_message(
            chat_id="oc_chat",
            message_id="om_card_thread",
            content=final_text,
        )
    )

    # 2 tool-step patches + 1 final edit = 3 patches
    assert len(patches) == 3
    last_id, last_card = patches[-1]
    assert last_id == "om_card_thread"

    # Final body still has the mention up top
    body = last_card["body"]["elements"]
    assert body[0]["tag"] == "markdown"
    assert (
        body[0]["content"]
        == "<at id=ou_thread_sender></at>\n\nall done — see results above"
    )

    # Collapsible 执行记录 panel survives
    panels = [e for e in body if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1
    labels = [
        d["text"]["content"]
        for d in panels[0]["elements"]
        if d.get("tag") == "div"
    ]
    assert labels == ["ls /", "a.py"]


# ---------------------------------------------------------------------------
# Negative paths: cron / missing-sender / intermediate patches
# ---------------------------------------------------------------------------


def test_cron_or_no_sender_path_does_not_inject_mention() -> None:
    """When the per-call holder is unset (cron delivery, ad-hoc CLI, system
    events with no inbound sender), the FINAL edit content has no ``<at`` tag.
    This is the safety net behind the ``hasattr(source, 'user_id')`` /
    Feishu-only guard in ``gateway.run._run_agent``."""
    # Sanity: no ContextVar set
    assert _MENTION_TARGET_HOLDER.get() is None

    adapter = _make_adapter()
    _register_card(adapter, "om_card_cron", main_text="…")
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    # _prepend_feishu_mention is the helper run.py uses unconditionally.
    # With no holder/open_id it returns the input verbatim.
    final_text = _prepend_feishu_mention("scheduled summary")
    assert "<at" not in final_text

    asyncio.run(
        adapter.edit_message(
            chat_id="oc_chat",
            message_id="om_card_cron",
            content=final_text,
        )
    )

    card = _captured_card(adapter._patch_card_message)
    body_content = card["body"]["elements"][0]["content"]
    assert "<at" not in body_content
    assert body_content == "scheduled summary"


def test_intermediate_tool_step_patches_do_not_carry_mention() -> None:
    """Intermediate tool-step patches go through ``append_tool_step`` →
    ``_patch_card_message`` directly without touching ``main_text``.
    Even when the per-call holder is set (i.e. an inbound user query is
    in flight), the intermediate card body must NOT contain ``<at`` —
    only the FINAL edit (which run.py does explicitly) prepends the @.

    Regression guard for requirement 1 of the task: 'do not @ on every
    patch, only on the final response'.
    """
    adapter = _make_adapter()
    _register_card(adapter, "om_card_mid", main_text="…")
    adapter._patch_card_message = AsyncMock(return_value=_ok_response())

    # Simulate the holder being live for the duration of a query.
    token = _MENTION_TARGET_HOLDER.set("ou_active_sender")
    try:
        # Tool-step patch — should NOT auto-inject.  ``append_tool_step``
        # uses the card's stored ``main_text`` ("…" placeholder), not the
        # ContextVar.
        asyncio.run(
            adapter.append_tool_step(
                "om_card_mid", {"tool": "bash", "label": "ls /"}
            )
        )
    finally:
        _MENTION_TARGET_HOLDER.reset(token)

    card = _captured_card(adapter._patch_card_message)
    body = card["body"]["elements"]
    # Markdown body still reflects the placeholder text — no @ injected.
    assert body[0]["tag"] == "markdown"
    assert body[0]["content"] == "…"
    assert "<at" not in body[0]["content"]
    # Tool step rendered in the collapsible panel
    panels = [e for e in body if e.get("tag") == "collapsible_panel"]
    assert len(panels) == 1


def test_placeholder_send_is_not_affected_by_mention_holder() -> None:
    """The placeholder ``send(content='…')`` happens BEFORE the agent runs
    (and run.py does not call ``_prepend_feishu_mention`` on the placeholder
    text — only on the FINAL edit).  Confirm that even with the holder set,
    a normal ``send`` invocation does not inject ``<at`` into the card body
    (defence in depth: the @ prepending is gated to the call site, not the
    adapter)."""
    adapter = _make_adapter()

    sent_payloads: list = []

    async def _capture_send(*, chat_id, msg_type, payload, reply_to=None, metadata=None):
        sent_payloads.append({"msg_type": msg_type, "payload": payload})
        resp = MagicMock()
        resp.success = lambda: True
        resp.code = 0
        resp.msg = "ok"
        data = MagicMock()
        data.message_id = "om_placeholder"
        resp.data = data
        return resp

    adapter._feishu_send_with_retry = AsyncMock(side_effect=_capture_send)

    token = _MENTION_TARGET_HOLDER.set("ou_user_in_flight")
    try:
        asyncio.run(
            adapter.send(
                chat_id="oc_chat",
                content="…",
                reply_to="om_anchor",
                metadata={"thread_id": "omt_topic"},
            )
        )
    finally:
        _MENTION_TARGET_HOLDER.reset(token)

    # The card payload that hit the wire must not carry the @-mention —
    # the holder is intentionally consulted only at the final-edit call site
    # in gateway/run.py, not inside the adapter's send/patch primitives.
    assert len(sent_payloads) == 1
    card = json.loads(sent_payloads[0]["payload"])
    assert card["body"]["elements"][0]["content"] == "…"
    assert "<at" not in sent_payloads[0]["payload"]


# ---------------------------------------------------------------------------
# Holder lifetime: per-call isolation (mirrors test_feishu_thread_card.py
# patterns — guards that the new ContextVar follows the same rules as
# _PRIMARY_CARD_HOLDER and does not leak across queries).
# ---------------------------------------------------------------------------


def test_mention_holder_default_is_none() -> None:
    """The ContextVar's default must be None so callers outside ``_run_agent``
    see no target and the helper degrades to a no-op."""
    assert _MENTION_TARGET_HOLDER.get() is None


def test_mention_holder_is_per_call_isolated() -> None:
    """Two queries with two distinct senders use two distinct mentions —
    the second query's holder does not leak the first sender's open_id."""
    # Turn 1
    token_1 = _MENTION_TARGET_HOLDER.set("ou_first")
    try:
        out_1 = _prepend_feishu_mention("first answer")
    finally:
        _MENTION_TARGET_HOLDER.reset(token_1)
    # Outside any holder
    assert _MENTION_TARGET_HOLDER.get() is None

    # Turn 2
    token_2 = _MENTION_TARGET_HOLDER.set("ou_second")
    try:
        out_2 = _prepend_feishu_mention("second answer")
    finally:
        _MENTION_TARGET_HOLDER.reset(token_2)

    assert out_1 == "<at id=ou_first></at>\n\nfirst answer"
    assert out_2 == "<at id=ou_second></at>\n\nsecond answer"
    assert "ou_first" not in out_2
    assert "ou_second" not in out_1


# ---------------------------------------------------------------------------
# Reviewer-flagged dead-code bug: post-finally placeholder edit at
# gateway/run.py:9010 must NOT rely on the ContextVar (already reset by the
# finally block — would silently drop the @-mention on the standard
# non-streaming Feishu thread-reply path).
# ---------------------------------------------------------------------------


def test_post_finally_placeholder_edit_still_injects_at_mention() -> None:
    """Regression test for the dead-code bug at ``gateway/run.py:9010``.

    The reviewer found that the most important @-mention call site — the
    post-finally placeholder card edit that runs on every standard
    non-streaming Feishu thread reply — was silently dropping the
    @-mention because:

      1. ``_run_agent`` opens a ``try``/``finally``.
      2. The ``finally`` block resets ``_MENTION_TARGET_HOLDER`` back to
         ``None`` (line 8963 in the pre-fix tree).
      3. The post-finally placeholder edit then called
         ``_prepend_feishu_mention(_final_text)`` with no ``open_id=``.
      4. The helper read the now-reset ContextVar, saw ``None``, and
         returned ``_final_text`` unchanged — the @ was dropped.

    This is the exact lifecycle the reviewer reproduced with 10 lines
    of Python.  The fix (Solution A) is to capture the sender's open_id
    in a local variable BEFORE the ``try`` block
    (``_feishu_mention_open_id``) and pass it explicitly to the helper at
    the post-finally call site:
    ``_prepend_feishu_mention(_final_text, open_id=_feishu_mention_open_id)``.

    This test asserts BOTH halves of the contract:

    - **Behavioural** — simulate the ContextVar lifecycle (set → enter
      ``try`` → reset in ``finally`` → post-finally call).  The buggy
      call shape ``_prepend_feishu_mention(text)`` returns text
      unchanged (documents the bug condition); the fixed call shape
      ``_prepend_feishu_mention(text, open_id=captured)`` injects the
      ``<at>`` tag.
    - **Structural** — inspect ``GatewayRunner._run_agent``'s source
      and assert the call site after ``_feishu_mention_target_var.reset``
      passes ``open_id=`` explicitly.  Pre-fix this fails (the call was
      ``_feishu_prepend_mention(_final_text)`` with no ``open_id=``);
      post-fix it passes.
    """
    import inspect
    import re

    import gateway.run as gateway_run

    # ── Behavioural part: reproduce the exact ContextVar lifecycle ──
    sender_open_id = "ou_post_finally_sender"
    captured_local = sender_open_id  # mirrors ``_feishu_mention_open_id``
    token = _MENTION_TARGET_HOLDER.set(captured_local)
    try:
        # Inside the try block: ContextVar is set → helper without
        # explicit open_id WOULD inject correctly here.
        assert _MENTION_TARGET_HOLDER.get() == sender_open_id
    finally:
        # Mirrors ``finally: _feishu_mention_target_var.reset(token)``
        _MENTION_TARGET_HOLDER.reset(token)

    # === post-finally: ContextVar is now None — the bug condition ===
    assert _MENTION_TARGET_HOLDER.get() is None

    # The buggy call shape (no ``open_id=``) silently drops the @ —
    # this is what production was doing before the fix.
    bug_shape_output = _prepend_feishu_mention("all done")
    assert "<at" not in bug_shape_output, (
        "Post-finally the ContextVar is None; helper without explicit"
        " open_id must return text unchanged.  This is the bug condition"
        " the fix addresses — Solution A passes open_id= explicitly."
    )

    # The fixed call shape (``open_id=`` passed from the local captured
    # before the try block) injects the @ correctly.
    fixed_shape_output = _prepend_feishu_mention(
        "all done", open_id=captured_local
    )
    assert (
        fixed_shape_output
        == "<at id=ou_post_finally_sender></at>\n\nall done"
    )

    # ── Structural part: assert production source uses the fix shape ──
    src = inspect.getsource(gateway_run.GatewayRunner._run_agent)
    finally_marker = "_feishu_mention_target_var.reset"
    finally_idx = src.find(finally_marker)
    assert finally_idx > 0, (
        "Couldn't locate the _feishu_mention_target_var.reset marker."
        "  Either the holder lifetime was refactored or the import"
        " alias was renamed; this test must be updated to track the"
        " new structure of _run_agent."
    )
    post_finally_src = src[finally_idx:]

    # The post-finally placeholder edit MUST pass open_id= explicitly.
    # Pre-fix the call was ``_feishu_prepend_mention(_final_text)`` —
    # no comma, no open_id — so this regex fails to match.  Post-fix
    # the call is ``_feishu_prepend_mention(_final_text, open_id=...)``
    # — the regex matches.  ``\s*`` handles the multi-line wrap.
    pattern = re.compile(
        r"_feishu_prepend_mention\s*\(\s*_final_text\s*,\s*open_id\s*=",
    )
    assert pattern.search(post_finally_src), (
        "Post-finally call to _feishu_prepend_mention(_final_text) must"
        " pass open_id= explicitly — otherwise the ContextVar is None at"
        " this point and the @-mention is silently dropped on every"
        " standard non-streaming Feishu thread reply"
        " (reviewer-flagged dead-code bug at gateway/run.py:9010)."
    )
