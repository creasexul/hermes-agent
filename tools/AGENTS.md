# tools/ -- Tool Registry, Schemas, Handlers & Dispatch

[父层索引](../AGENTS.md)

Central registry pattern: each tool file calls `registry.register()` at import time to declare its schema, handler, toolset, and availability check. `model_tools.py` triggers discovery by importing all tool modules, then exposes the public API consumed by `run_agent.py`, `cli.py`, and `batch_runner.py`. All handlers return JSON strings; the registry wraps dispatch with error handling.

## Tool Registration Chain
```
tools/registry.py            ToolRegistry singleton (zero tool-file deps)
  ↓ tools/*_tool.py           registry.register(name, toolset, schema, handler, check_fn, requires_env)
  ↓ model_tools._discover_tools()  importlib.import_module() per module → MCP → plugins
  ↓ get_tool_definitions()    filters by toolset + check_fn() → OpenAI-format schema list
  ↓ handle_function_call()    registry.dispatch(name, args) → handler(args, **kw) → JSON string
```

### Core Infrastructure
| File | Purpose |
|------|---------|
| registry.py | Singleton `ToolRegistry`: register, dispatch, schema retrieval; exports `registry`, `tool_error()`, `tool_result()` |
| __init__.py | Package namespace; lazy imports only |
| budget_config.py | Configurable per-tool result size caps (`DEFAULT_RESULT_SIZE_CHARS`) |
| tool_result_storage.py | Persist large tool outputs to disk instead of truncating |
| tool_backend_helpers.py | Shared helpers for backend selection (Firecrawl/Exa/etc.) |
| debug_helpers.py | Shared debug session logging for multiple tools |
| interrupt.py | Per-thread interrupt signaling across concurrent sessions |
### Browser & Web (toolsets: `browser`, `web`)
| File | Purpose |
|------|---------|
| web_tools.py | `web_search`, `web_extract`, `web_crawl` via Exa/Firecrawl/Tavily |
| browser_tool.py | 10 browser_* tools: navigate/snapshot/click/type/scroll/back/press/get_images/vision/console |
| browser_camofox.py | Camofox anti-detection browser backend via REST API |
| browser_camofox_state.py | Profile-scoped identity/state paths for Camofox persistence |
| website_policy.py | URL blocklist enforcement for web/browser tools |
| url_safety.py | SSRF protection -- blocks private/internal network addresses |
| openrouter_client.py | Shared AsyncOpenAI client for LLM-powered content extraction |
### Terminal & Execution (toolsets: `terminal`, `code_execution`)
| File | Purpose |
|------|---------|
| terminal_tool.py | `terminal` -- shell execution; factory selects backend via TERMINAL_ENV |
| code_execution_tool.py | `execute_code` -- LLM writes Python that calls tools via RPC |
| process_registry.py | `process` tool -- long-running process lifecycle (start/stop/status/logs) |
| ansi_strip.py | Strip ANSI escape sequences from subprocess output |
| env_passthrough.py | Env var passthrough registry for sandboxed skill execution |
| credential_files.py | File mount registry for remote backends (Docker/Modal/SSH) |
### environments/ -- Execution backends (`BaseEnvironment` ABC in base.py)
| File | Purpose |
|------|---------|
| local.py | Local shell | docker.py | Docker (cap-drop, resource limits, bind mounts) |
| ssh.py | SSH with ControlMaster | modal.py | Modal cloud sandbox via native SDK |
| managed_modal.py | Modal via tool-gateway | modal_utils.py | Shared Modal command prep |
| singularity.py | Singularity/Apptainer with writable overlays |
| daytona.py | Daytona cloud sandbox | file_sync.py | Mtime file sync (SSH/Modal/Daytona) |
### browser_providers/ -- Cloud browser adapters (`CloudBrowserProvider` ABC in base.py)
browserbase.py (Browserbase), browser_use.py (Browser Use), firecrawl.py (Firecrawl)
### File Operations (toolset: `file`)
| File | Purpose |
|------|---------|
| file_tools.py | `read_file`, `write_file`, `patch`, `search_files` -- 4 registered tools |
| file_operations.py | Backend implementation for file read/write/patch/search |
| patch_parser.py | V4A patch format parser (codex/cline compatible) |
| fuzzy_match.py | Multi-strategy fuzzy matching for patch/replace operations |
| binary_extensions.py | Binary file extension list for skipping text operations |
### Skills System (toolset: `skills`)
| File | Purpose |
|------|---------|
| skills_tool.py | `skills_list`, `skill_view` -- skill listing and inspection |
| skill_manager_tool.py | `skill_manage` -- agent-driven skill CRUD |
| skills_hub.py | Source adapters and hub state management (library, not a tool) |
| skills_sync.py | Manifest-based seeding/updating of bundled skills |
| skills_guard.py | Security scanner for externally-sourced skills |
### Voice & Audio (toolset: `tts`)
| File | Purpose |
|------|---------|
| tts_tool.py | `text_to_speech` -- multi-provider TTS (OpenAI/ElevenLabs/local) |
| transcription_tools.py | STT helpers (Whisper/OpenAI); consumed by voice_mode |
| voice_mode.py | Push-to-talk audio recording/playback for CLI |
| neutts_synth.py | Subprocess NeuTTS synthesis helper (isolates ~500MB model) |
### Agent Orchestration (toolsets: `delegation`, `moa`)
| File | Purpose |
|------|---------|
| delegate_tool.py | `delegate_task` -- spawn child AIAgent with isolated context |
| mixture_of_agents_tool.py | `mixture_of_agents` -- parallel multi-model synthesis |
| workflow_tool.py | `workflow` -- YAML DAG-based multi-agent orchestration (6 node types) |
| workflow_planner.py | Auto-planning, templates, scheduler, and reviewer for workflows |
### MCP Integration (toolset: dynamic `mcp_{server}_{tool}`)
| File | Purpose |
|------|---------|
| mcp_tool.py | MCP client lifecycle, dynamic tool discovery via registry, stdio/HTTP transport |
| mcp_oauth.py | OAuth 2.1 + PKCE authorization for authenticated MCP servers |
| osv_check.py | OSV malware check for MCP packages before npx/uvx launch |
| managed_tool_gateway.py | Route vendor API calls through Nous-hosted gateway |
### Security Helpers (not tools -- consumed by tool files)
| File | Purpose |
|------|---------|
| path_security.py | `validate_within_dir()`, `has_traversal_component()` path traversal guards |
| tirith_security.py | Tirith policy engine: homograph URLs, pipe-to-interpreter, injection scanning |
| checkpoint_manager.py | Transparent filesystem snapshots via shadow git repos (not LLM-visible) |
### Other Tools
| File | Purpose |
|------|---------|
| vision_tools.py | `vision_analyze` (toolset: `vision`) -- image understanding via multimodal LLMs |
| image_generation_tool.py | `image_generate` (toolset: `image_gen`) -- DALL-E/fal/Stability |
| homeassistant_tool.py | ha_* 4 tools (toolset: `homeassistant`) -- entity/service control |
| send_message_tool.py | `send_message` (toolset: `messaging`) -- cross-platform messaging |
| memory_tool.py | `memory` (toolset: `memory`) -- persistent cross-session memory store |
| session_search_tool.py | `session_search` (toolset: `session_search`) -- FTS5 over past sessions |
| todo_tool.py | `todo` (toolset: `todo`) -- in-memory task list with TodoStore |
| clarify_tool.py | `clarify` (toolset: `clarify`) -- structured clarifying questions |
| cronjob_tools.py | `cronjob` (toolset: `cronjob`) -- scheduled task CRUD |
| rl_training_tool.py | rl_* 10 tools (toolset: `rl`) -- RL training via Atropos |

## 新增工具步骤 (Adding a New Tool)
1. Create `tools/my_tool.py` with a handler returning JSON via `tool_result()`/`tool_error()`
2. Define the OpenAI-format schema dict with `name`, `description`, and `parameters`
3. Call `registry.register(name=..., toolset=..., schema=..., handler=..., check_fn=..., requires_env=[...])` at module level
4. Add `"tools.my_tool"` to the `_modules` list in `model_tools._discover_tools()`
5. Add tool name to appropriate toolset in `toolsets.py` (`_HERMES_CORE_TOOLS` or platform list)
6. If the tool needs approval for dangerous operations, integrate with `tools.approval`
