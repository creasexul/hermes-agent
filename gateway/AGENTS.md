# gateway/ — Multi-platform messaging gateway

Connects the Hermes agent to 18 messaging platforms (Telegram, Discord, Slack, WhatsApp, etc.). GatewayRunner orchestrates adapter lifecycle, user authorization, session management, slash command dispatch, streaming delivery, and cron output routing. Delegates AI/model logic to `run_agent.AIAgent`.

## File Index

### Core Orchestration
| File | Purpose | Key symbols |
|------|---------|-------------|
| `run.py` (9K lines) | Central orchestrator: adapter startup, message dispatch, slash command handling, agent invocation | `GatewayRunner`, `start_gateway()`, `_handle_message()`, `_run_agent()` |
| `config.py` | Gateway config loading, `Platform` enum (19 values), reset policies, home channels | `GatewayConfig`, `PlatformConfig`, `Platform`, `load_gateway_config()` |
| `__init__.py` | Public API re-exports | `GatewayConfig`, `SessionStore`, `DeliveryRouter` |

### Session Management
| File | Purpose | Key symbols |
|------|---------|-------------|
| `session.py` | Session storage (SQLite + JSONL fallback), reset policy evaluation, context prompt injection | `SessionStore`, `SessionEntry`, `SessionSource`, `build_session_context_prompt()` |
| `session_context.py` | Task-local `contextvars` for async-safe session state (replaces `os.environ`) | `set_session_vars()`, `get_session_env()` |

### Message Delivery
| File | Purpose | Key symbols |
|------|---------|-------------|
| `stream_consumer.py` | Bridges sync agent deltas to async progressive message editing (rate-limited) | `GatewayStreamConsumer`, `StreamConsumerConfig` |
| `delivery.py` | Routes cron/agent output to target platforms via home channels or explicit IDs | `DeliveryRouter`, `DeliveryTarget` |
| `mirror.py` | Appends delivery-mirror records to target session transcripts for cross-platform context | `mirror_to_session()` |
| `channel_directory.py` | Cached map of reachable channels/contacts, refreshed every 5 min | `DIRECTORY_PATH`, `_normalize_channel_query()` |

### Lifecycle & Security
| File | Purpose | Key symbols |
|------|---------|-------------|
| `pairing.py` | Code-based DM approval flow for unknown users (8-char codes, rate-limited, 1h expiry) | `PairingStore`, `generate_code()` |
| `hooks.py` | Event hook system: discovers `~/.hermes/hooks/` handlers, fires at lifecycle points | `HookRegistry`, `emit()` |
| `status.py` | PID-file guard, runtime health/state JSON, scoped locks for single-instance enforcement | `get_running_pid()`, `write_runtime_status()` |
| `restart.py` | Restart constants and drain timeout parsing | `GATEWAY_SERVICE_RESTART_EXIT_CODE` |
| `display_config.py` | Per-platform display settings resolver (tool_progress, streaming, reasoning) | `resolve_display_setting()` |
| `sticker_cache.py` | Telegram sticker description cache (vision-described, keyed by file_unique_id) | `CACHE_PATH` |
| `builtin_hooks/boot_md.py` | Runs `~/.hermes/BOOT.md` instructions on gateway startup | `BOOT_FILE` |

### Platform Adapters
| Submodule | Files | Purpose | Index |
|-----------|-------|---------|-------|
| `platforms/` | 23 | 18 platform adapters + base ABC + helpers | [AGENTS.md](platforms/AGENTS.md) |

## Message Flow

```
Platform SDK event
  → Adapter.handle_message(MessageEvent)
  → GatewayRunner._handle_message(event)
    → auth check (_is_user_authorized / pairing flow)
    → slash command dispatch (event.get_command() → canonical → handler)
    → _handle_message_with_agent(event)
      → SessionStore.get_or_create_session(source)
      → set_session_vars() (contextvars, async-safe)
      → build_session_context_prompt()
      → _run_agent() in thread pool
        → AIAgent.run_conversation(stream_delta_callback=consumer.on_delta)
      → GatewayStreamConsumer progressively edits platform message
      → final response → adapter.send()
```

## Session Model

```
SessionSource (platform, chat_id, user_id, chat_type)
  → session_key = f"{platform}:{chat_id}" (or with thread_id)
  → SessionStore._entries[session_key] → SessionEntry (session_id, tokens, cost)
  → Transcript: SQLite (SessionDB) + legacy JSONL fallback
  → Reset: policy-based (idle timeout / daily / manual /new)
  → Context isolation: contextvars (session_context.py) per asyncio task
```

## Slash Command Dispatch

```
event.get_command() → hermes_cli/commands.py:resolve_command()
  → canonical name → GatewayRunner._handle_<command>_command()
  → fallback: plugin commands → skill commands → send to agent as instruction
```
