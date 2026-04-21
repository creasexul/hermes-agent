"""Tests for the Feishu in-thread reply card builder.

Covers ``_build_thread_reply_card`` only — no network, no Feishu connection.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

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

from gateway.platforms.feishu import _build_thread_reply_card  # noqa: E402


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


if __name__ == "__main__":  # pragma: no cover
    sys.exit(pytest.main([__file__, "-v"]))
