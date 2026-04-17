# plugins/ — Plugin System

[父层索引](../AGENTS.md)

Two extension points: **memory providers** (8 implementations) and **context engines**.
Discovery via `plugin.yaml` metadata + `register(ctx)` entry point.
Only one provider/engine active at a time, selected in `config.yaml`.

## Package & Discovery

| File | Role |
|------|------|
| `__init__.py` | Package marker (comment only) |
| `memory/__init__.py` | Memory provider discovery: `discover_memory_providers()`, `load_memory_provider(name)`, `_ProviderCollector`, `discover_plugin_cli_commands()` |
| `context_engine/__init__.py` | Context engine discovery: `discover_context_engines()`, `load_context_engine(name)`, `_EngineCollector` (no plugins installed yet) |

## Memory Provider Plugins

Each subdirectory under `memory/` contains `plugin.yaml` (metadata) + `__init__.py` (MemoryProvider subclass + `register(ctx)` entry point).

| Plugin | Description | Extra Files |
|--------|-------------|-------------|
| `byterover/` | Persistent knowledge tree via `brv` CLI, tiered retrieval | -- |
| `hindsight/` | Knowledge graph, entity resolution, multi-strategy retrieval (hindsight-client SDK) | -- |
| `holographic/` | Local SQLite fact store with FTS5 search, trust scoring, HRR compositional retrieval | `holographic.py` (HRR vector algebra), `store.py` (SQLite + entity resolution), `retrieval.py` (hybrid FTS5/Jaccard search) |
| `honcho/` | AI-native cross-session user modeling, dialectic Q&A, semantic search, conclusions | `client.py` (config resolution + SDK client), `session.py` (conversation session mgmt), `cli.py` (setup/status/sessions/map/peer subcommands) |
| `mem0/` | Server-side LLM fact extraction, semantic search with reranking, deduplication (Mem0 API) | -- |
| `openviking/` | Volcengine context DB with filesystem-style browsing, tiered context loading, auto-extraction | -- |
| `retaindb/` | Cloud memory API with hybrid search, SQLite write-behind queue, dialectic synthesis | -- |
| `supermemory/` | Semantic long-term memory with profile recall, session ingest, hybrid search modes | -- |

## Plugin Protocol

**plugin.yaml** -- Metadata fields: `name`, `version`, `description`; optional: `pip_dependencies`, `external_dependencies`, `requires_env`, `hooks`.

**register(ctx)** -- Entry point called with a collector object. Must call `ctx.register_memory_provider(instance)` or `ctx.register_context_engine(instance)`.

**MemoryProvider ABC** (`agent/memory_provider.py`) -- Required: `name`, `is_available()`, `initialize()`, `get_tool_schemas()`, `handle_tool_call()`. Optional: `system_prompt_block()`, `prefetch()`, `sync_turn()`, `shutdown()`, `on_session_end()`, `on_pre_compress()`, `on_memory_write()`, `on_delegation()`, `get_config_schema()`, `save_config()`.

**Fallback loading** -- If no `register()` function exists, discovery scans module attributes for a MemoryProvider/ContextEngine subclass and instantiates it directly.

## Business Flow: Plugin Discovery & Registration

```
config.yaml[memory.provider] → run_agent.py reads provider name
  → plugins.memory.load_memory_provider(name)
    → _load_provider_from_dir(plugins/memory/<name>/)
      → importlib loads __init__.py + registers submodules for relative imports
      → calls module.register(_ProviderCollector())
      → _ProviderCollector.register_memory_provider() captures instance
    → returns MemoryProvider instance (or None on failure)
  → MemoryManager.add_provider(instance) alongside built-in provider
  → provider.initialize(session_id) — ready for agent loop
```

## Business Flow: CLI Command Registration

```
hermes_cli/main.py argparse setup
  → plugins.memory.discover_plugin_cli_commands()
    → _get_active_memory_provider() reads config.yaml[memory.provider]
    → loads only active plugin's cli.py (lightweight, no full plugin import)
    → returns {name, setup_fn, handler_fn} for argparse subcommand
```

## Adding a New Plugin

1. Create `plugins/memory/<name>/` with `__init__.py` and `plugin.yaml`
2. Implement a class extending `agent.memory_provider.MemoryProvider`
3. Define `register(ctx)` that calls `ctx.register_memory_provider(YourProvider())`
4. Fill `plugin.yaml` with name, version, description, and any dependencies
5. Set `memory.provider: <name>` in `config.yaml` to activate
6. Optionally add `cli.py` with `register_cli(subparser)` for CLI subcommands

## Key Integration Points

- **run_agent.py** -- Reads `memory.provider` / `context.engine` from config, loads plugin, wires into agent
- **agent/memory_manager.py** -- Orchestrates built-in + one external provider through the MemoryProvider lifecycle
- **agent/memory_provider.py** -- MemoryProvider ABC defining the full contract
- **hermes_cli/plugins_cmd.py** -- `hermes plugins` lists available providers and engines
- **hermes_cli/memory_setup.py** -- Interactive provider selection during `hermes setup`
