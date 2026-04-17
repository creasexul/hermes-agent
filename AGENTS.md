# Hermes Agent

Self-improving AI agent by Nous Research — tool-calling conversation loop with multi-platform messaging gateway, skills system, memory, and RL training environments.

## Quick Start

```bash
source venv/bin/activate          # ALWAYS activate before running Python
hermes                            # Interactive CLI
hermes gateway start              # Start messaging gateway
python -m pytest tests/ -q        # Full test suite (~3000 tests)
```

**User config:** `~/.hermes/config.yaml` (settings), `~/.hermes/.env` (API keys)
**Profiles:** `HERMES_HOME` env var — always use `get_hermes_home()` from `hermes_constants`, never hardcode `~/.hermes`.

## Module Index

**Rule: before entering any subdirectory, `cat <dir>/AGENTS.md` if it exists.**

| Directory | Responsibility | Files | Index |
|-----------|---------------|-------|-------|
| `tools/` | Tool registry, schemas, handlers, dispatch (one file per tool) | 72 | [AGENTS.md](tools/AGENTS.md) |
| `hermes_cli/` | CLI entry point, subcommands, setup, config, auth, model catalogs | 47 | [AGENTS.md](hermes_cli/AGENTS.md) |
| `environments/` | Atropos RL training environments, benchmarks, tool-call parsers | 43 | [AGENTS.md](environments/AGENTS.md) |
| `gateway/` | Multi-platform messaging gateway (18 adapters) | 40 | [AGENTS.md](gateway/AGENTS.md) |
| `gateway/platforms/` | Platform adapter implementations + BasePlatformAdapter ABC | 20 | [AGENTS.md](gateway/platforms/AGENTS.md) |
| `agent/` | Prompt assembly, context compression, API adapters, failover, memory | 27 | [AGENTS.md](agent/AGENTS.md) |
| `plugins/` | Plugin system: memory providers (8) and context engines | 17 | [AGENTS.md](plugins/AGENTS.md) |

### Root-Level Modules (no separate index)

| Path | Responsibility |
|------|---------------|
| `run_agent.py` | `AIAgent` class — core synchronous conversation loop (`run_conversation()`, `chat()`) |
| `model_tools.py` | Tool discovery (`_discover_tools()`), dispatch bridge (`handle_function_call()`) |
| `toolsets.py` | Toolset definitions, `_HERMES_CORE_TOOLS`, platform-specific toolset resolution |
| `cli.py` | `HermesCLI` — interactive TUI (prompt_toolkit + Rich) |
| `hermes_state.py` | `SessionDB` — SQLite session store with FTS5 full-text search |
| `hermes_constants.py` | `get_hermes_home()`, `display_hermes_home()`, shared constants |
| `acp_adapter/` | Agent Client Protocol server for editor integrations (VS Code, Zed, JetBrains) — 9 files |
| `cron/` | Scheduled tasks: `jobs.py` (CRUD + persistence), `scheduler.py` (tick + multi-platform delivery) — 3 files |
| `web/` | React + Vite dashboard (config, env, sessions). Backend: `hermes_cli/web_server.py` — 5 files |
| `skills/` | Default skill instruction files (markdown, 26 categories, 78 skills) |
| `optional-skills/` | Optional skill packs, `hermes skills browse/install` |
| `tests/` | Pytest suite (~3000 tests), mirrors source structure. `conftest.py` has `_isolate_hermes_home` autouse fixture |

## File Dependency Chain

```
tools/registry.py  (no deps — imported by all tool files)
       ↑
tools/*.py  (each calls registry.register() at import time)
       ↑
model_tools.py  (imports tools/registry, triggers tool discovery)
       ↑
run_agent.py  ←── cli.py, gateway/run.py, batch_runner.py, environments/
```

## Core Business Flows

### 1. User Message → Agent Response (CLI)
```
cli.py run_chat_loop() → run_agent.py AIAgent.run_conversation()
  → agent/prompt_builder.py build system prompt → API call
  → tool_call? → model_tools.py handle_function_call() → tools/registry.py dispatch → loop
```

### 2. Platform Message → Agent Response (Gateway)
```
gateway/platforms/<adapter>.py receives message → gateway/run.py GatewayRunner._dispatch()
  → auth + session lookup → AIAgent.run_conversation() → stream_consumer → adapter.send()
```

### 3. Adding a New Tool
See [tools/AGENTS.md](tools/AGENTS.md). Summary: create `tools/your_tool.py` with `registry.register()`, add import in `model_tools.py:_discover_tools()`, add to `toolsets.py`.

### 4. Adding a Slash Command
See [hermes_cli/AGENTS.md](hermes_cli/AGENTS.md). Summary: add `CommandDef` to `hermes_cli/commands.py`, add handler in `cli.py` and optionally `gateway/run.py`.

## Key Policies

- **Prompt caching**: do NOT alter past context, change toolsets, or rebuild system prompts mid-conversation. Cache-breaking = dramatically higher costs.
- **Profile safety**: use `get_hermes_home()` for code paths, `display_hermes_home()` for display. Never hardcode `~/.hermes`.
- **Tool handlers**: must return JSON strings. Cross-tool schema references must be dynamic (see `model_tools.py`).
- **Tests**: never write to `~/.hermes/`. The `_isolate_hermes_home` fixture redirects to temp dir.
- **No `simple_term_menu`**: rendering bugs in tmux/iTerm2. Use `curses` instead.
- **No `\033[K`**: leaks as literal text under `prompt_toolkit`. Use space-padding.

## Testing

```bash
source venv/bin/activate
python -m pytest tests/ -q                    # Full suite
python -m pytest tests/tools/ -q              # Tool tests
python -m pytest tests/gateway/ -q            # Gateway tests
python -m pytest tests/hermes_cli/ -q         # CLI tests
python -m pytest tests/test_model_tools.py -q # Toolset resolution
```
