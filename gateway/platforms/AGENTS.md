# Platform Adapters — Messaging platform integrations extending BasePlatformAdapter

Each adapter connects the gateway to one messaging platform. Adapters receive inbound
messages via platform SDKs or webhooks, normalize them into `MessageEvent` objects, and
hand them to `GatewayRunner._handle_message()`. Outbound responses flow back through
`adapter.send()` with platform-specific formatting (Markdown, HTML, cards).

## File Index

### Base & Helpers
| File | Purpose |
|------|---------|
| `base.py` | `BasePlatformAdapter` ABC, `MessageEvent`/`SendResult`/`MessageType` types, retry logic, message splitting, media caching helpers |
| `helpers.py` | `MessageDeduplicator` (TTL cache), `ThreadParticipationTracker`, text batching, markdown stripping |
| `__init__.py` | Re-exports `BasePlatformAdapter`, `MessageEvent`, `SendResult` |

### Platform Adapters
| File | Platform | Key features |
|------|----------|-------------|
| `telegram.py` | Telegram | python-telegram-bot; groups, DMs, forums, inline keyboards, stickers, voice, media |
| `telegram_network.py` | Telegram (network) | Hostname-preserving fallback transport for blocked api.telegram.org DNS |
| `discord.py` | Discord | discord.py; servers, DMs, threads, slash commands, reactions, embeds, voice |
| `slack.py` | Slack | slack-bolt Socket Mode; channels, DMs, threads, slash commands, Block Kit |
| `whatsapp.py` | WhatsApp | Multi-backend bridge (Business API / whatsapp-web.js / Baileys via Node subprocess) |
| `signal.py` | Signal | signal-cli HTTP daemon; SSE inbound, JSON-RPC outbound, groups, attachments |
| `matrix.py` | Matrix | mautrix SDK; optional E2EE, rooms, threads, reactions, cross-signing |
| `mattermost.py` | Mattermost | REST v4 + WebSocket; channels, DMs, threads (no external lib, uses aiohttp) |
| `email.py` | Email | IMAP polling + SMTP send; subject-threaded conversations |
| `sms.py` | SMS (Twilio) | Twilio REST + aiohttp webhook server; signature validation |
| `homeassistant.py` | Home Assistant | HA WebSocket API; state-change events, persistent notifications |
| `dingtalk.py` | DingTalk | dingtalk-stream SDK; DMs, groups, markdown responses |
| `feishu.py` | Feishu/Lark | WebSocket + webhook; DMs, group @mentions, cards, reactions, serial processing |
| `wecom.py` | WeCom (bot) | WebSocket AI Bot gateway; markdown messages, media upload |
| `wecom_callback.py` | WeCom (callback) | HTTP callback mode for self-built enterprise apps; encrypted XML inbound |
| `wecom_crypto.py` | WeCom (crypto) | AES-CBC encryption compatible with Tencent WXBizMsgCrypt SDK |
| `weixin.py` | WeChat | iLink Bot API; long-poll inbound, AES-128-ECB media CDN, QR login |
| `bluebubbles.py` | iMessage | BlueBubbles macOS server; REST + webhooks, tapbacks, read receipts |
| `webhook.py` | Generic webhook | aiohttp server; HMAC validation, route-based event filtering, idempotency cache |
| `api_server.py` | OpenAI-compat API | HTTP /v1/chat/completions, /v1/responses, /v1/runs SSE, /v1/models |
| `ADDING_A_PLATFORM.md` | (docs) | Step-by-step checklist for integrating a new platform adapter |

## Adapter Contract

`BasePlatformAdapter` (ABC) requires these methods:

| Method | Signature | Purpose |
|--------|-----------|---------|
| `connect` | `async connect() -> bool` | Connect to platform, start listeners; return True on success |
| `disconnect` | `async disconnect() -> None` | Stop listeners, close connections, cancel tasks |
| `send` | `async send(chat_id, text, ...) -> SendResult` | Send a text message to a chat |
| `get_chat_info` | `async get_chat_info(chat_id) -> dict` | Return `{name, type, chat_id}` metadata |

Optional methods with default stubs in base:

| Method | Purpose |
|--------|---------|
| `send_typing(chat_id)` | Send typing indicator |
| `send_image(chat_id, url, caption)` | Send image from URL |
| `send_image_file(chat_id, path, caption)` | Send image from local file |
| `send_document(chat_id, path, caption)` | Send file attachment |
| `send_voice(chat_id, path)` | Send voice message |
| `send_video(chat_id, path, caption)` | Send video |
| `send_animation(chat_id, path, caption)` | Send GIF/animation |

Each adapter must also export `check_<platform>_requirements() -> bool`.

## Inbound Message Flow (per adapter)

```
Platform SDK callback / webhook / polling
  -> parse to MessageEvent(text, source=SessionSource(...), media_urls, ...)
  -> self.handle_message(event)  [inherited from BasePlatformAdapter]
  -> GatewayRunner._handle_message(event)
```

Key patterns: use `self.build_source(...)` for `SessionSource` construction,
filter self-messages to prevent reply loops, implement reconnection with
exponential backoff + jitter for streaming connections, set `MAX_MESSAGE_LENGTH`.

## Adding a New Platform

See [ADDING_A_PLATFORM.md](ADDING_A_PLATFORM.md) for the full checklist. Summary:

1. Create `gateway/platforms/<platform>.py` subclassing `BasePlatformAdapter`
2. Implement required methods: `connect`, `disconnect`, `send`, `get_chat_info`
3. Add platform to `Platform` enum in `gateway/config.py`
4. Add `check_<platform>_requirements()` for dependency validation
5. Register adapter in `GatewayRunner._create_adapter()` (run.py)
6. Add config schema to `gateway/config.py` PlatformConfig loading
7. Test: self-message filtering, reconnection backoff, media handling

## Related

- [Parent index](../AGENTS.md)
- [Adding a platform checklist](ADDING_A_PLATFORM.md)
