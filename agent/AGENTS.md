# agent/ -- Agent Internals

[父层索引](../AGENTS.md)

Modules extracted from `run_agent.py`. System prompt assembly, context compression,
API adapters (Anthropic, Copilot ACP), credential pool rotation, error failover,
memory provider orchestration, display/analytics, and security redaction. 27 files.

## File Map

### Prompt System
| File | Purpose |
|------|---------|
| `prompt_builder.py` | System prompt assembly: identity + skills index + context files + toolsets + personality + safety; prompt-injection scanning for context files |
| `prompt_caching.py` | Anthropic `system_and_3` cache strategy -- 4 breakpoints (system + last 3 msgs), ~75% input cost reduction |
| `skill_utils.py` | Skill metadata: frontmatter parsing, platform matching, disabled-skill filtering |
| `skill_commands.py` | Slash-command dispatch shared by CLI and gateway for `/skill-name` and built-ins like `/plan` |
| `subdirectory_hints.py` | Lazy discovery of AGENTS.md / .cursorrules in subdirectories visited during tool calls |
| `context_references.py` | `@file`, `@url`, `@diff`, `@staged` reference expansion in user messages |

### Compression
| File | Purpose |
|------|---------|
| `context_engine.py` | ABC for pluggable context engines; defines `should_compress` / `compress` / lifecycle hooks; config-driven selection |
| `context_compressor.py` | Default `ContextEngine` impl: lossy summarization via auxiliary model, tool-output pruning, head + tail protection |
| `manual_compression_feedback.py` | User-facing before/after stats for `/compact` command |

### API Adapters
| File | Purpose |
|------|---------|
| `anthropic_adapter.py` | Bidirectional OpenAI-format <-> Anthropic Messages API translation; thinking budgets, OAuth/API-key auth, output-limit lookup |
| `copilot_acp_client.py` | OpenAI-compatible shim wrapping `copilot --acp` subprocess as chat-style backend |
| `auxiliary_client.py` | Side-task router: resolves best provider (OpenRouter > Portal > Custom > Codex > Anthropic > ...) for compression, vision, search; auto-fallback on 402 |
| `smart_model_routing.py` | Cheap-vs-strong model routing heuristics based on keyword/complexity analysis of user message |

### Failover & Reliability
| File | Purpose |
|------|---------|
| `error_classifier.py` | `FailoverReason` enum taxonomy (auth, billing, rate_limit, overloaded, context_overflow, ...) + `ClassifiedError` with recovery hints |
| `credential_pool.py` | Multi-credential pool with rotation strategies (fill_first, round_robin, random, least_used); cooldown tracking; persistent storage |
| `retry_utils.py` | `jittered_backoff()` -- decorrelated exponential backoff to prevent thundering-herd retries |
| `rate_limit_tracker.py` | Captures `x-ratelimit-*` headers (RPM/TPM/RPH/TPH) from provider responses for `/usage` display |

### Model & Pricing
| File | Purpose |
|------|---------|
| `model_metadata.py` | Context lengths, token estimation (`estimate_messages_tokens_rough`), provider-prefix stripping, OpenRouter model fetch |
| `models_dev.py` | models.dev registry integration: 4000+ models / 109+ providers; bundled snapshot + disk cache + background refresh |
| `usage_pricing.py` | Per-model cost calculation from provider pricing APIs / official docs / user overrides |

### Memory
| File | Purpose |
|------|---------|
| `memory_provider.py` | ABC for pluggable memory providers: initialize, prefetch, sync_turn, tool schemas, shutdown lifecycle |
| `memory_manager.py` | Orchestrates built-in + at most ONE external provider; wires prefetch / sync_turn / tool schemas into run_agent |

### Display & Analytics
| File | Purpose |
|------|---------|
| `display.py` | CLI spinner, kawaii faces, ANSI-colored tool preview formatting, diff coloring |
| `insights.py` | `InsightsEngine`: token consumption, cost estimates, tool-usage patterns, model/platform breakdowns from SQLite state DB |
| `title_generator.py` | Auto-generate short session titles async via auxiliary LLM after first exchange |
| `trajectory.py` | Trajectory file saving for batch evaluation runs |

### Security
| File | Purpose |
|------|---------|
| `redact.py` | Regex-based secret redaction (API keys, tokens, credentials) in logs and tool output; prefix-pattern matching with partial masking |

### Package
| File | Purpose |
|------|---------|
| `__init__.py` | Package docstring; no public exports |

## Business Flows

### Prompt Assembly Chain
```
prompt_builder: scan context files (injection filter) + load identity/skills/personality
  -> skill_utils: parse skill frontmatter, filter by platform -> build skills index
  -> context_references: expand @file/@url/@diff in user message
  -> subdirectory_hints: lazily inject per-directory AGENTS.md during tool calls
  -> prompt_caching: insert 4 cache_control breakpoints (system + last 3 msgs)
  -> memory_manager: append memory system_prompt_block + prefetch context
  => final system prompt + cached messages passed to API adapter
```

### Compression Pipeline
```
context_engine.should_compress(): prompt_tokens > threshold (75% of context_length)
  -> context_compressor: prune old tool outputs (cheap pre-pass, no LLM)
  -> context_compressor: protect head (first 3 msgs) + tail (~20K tokens)
  -> auxiliary_client.call_llm(): summarize middle turns via cheapest model
  -> context_compressor: iteratively update previous summary on re-compaction
  => compressed message list returned to run_agent.py
```

### Failover Chain
```
API call fails -> error_classifier.classify_api_error() -> ClassifiedError
  -> should_rotate_credential? -> credential_pool: rotate to next key by strategy
  -> should_compress? -> context_compressor: emergency compaction
  -> retryable? -> retry_utils.jittered_backoff(attempt) -> wait -> retry
  -> should_fallback? -> smart_model_routing / auxiliary_client: switch provider
  -> rate_limit_tracker: update remaining quota from response headers
  => not retryable + no fallback -> abort with user-facing error
```
