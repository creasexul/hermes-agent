# hermes_cli/ -- CLI Entry Point & Subcommands
[Parent index](../AGENTS.md)

CLI front-end: `hermes` binary (main.py 6K lines), all subcommands, setup wizard, auth/OAuth, model catalogs, skin engine, plugins, diagnostics. 47 files.

## File Map
### Entry & Dispatch
| File | Role |
|------|------|
| `__init__.py` | Package metadata: `__version__`, `__release_date__` |
| `main.py` | **Main entry point** (6K lines). `main()` builds argparse with `add_subparsers`, 30+ `cmd_*` handlers dispatch to sibling modules. Profile override before imports, `.env` loading, logging init. |
| `commands.py` | Central `COMMAND_REGISTRY` of `CommandDef` dataclasses. Single source of truth for CLI help, gateway dispatch, autocomplete. `resolve_command()`, `rebuild_lookups()`. |

### Auth & Credentials
| File | Role |
|------|------|
| `auth.py` | Multi-provider auth (3K lines). OAuth device-code flows (Nous, Codex, Qwen, Copilot), API key providers. `PROVIDER_REGISTRY`, `resolve_provider()`, token refresh, `auth.json` file-locked. |
| `copilot_auth.py` | GitHub Copilot OAuth device-code flow + token exchange. |
| `auth_commands.py` | `hermes auth add/list/remove/reset` -- credential pool management CLI. |
| `runtime_provider.py` | Runtime credential resolution shared by CLI/gateway/cron. Merges config + auth + env + credential pool. |
| `nous_subscription.py` | Nous subscription feature detection and managed-tool capabilities. |

### Config & Environment
| File | Role |
|------|------|
| `config.py` | Config management (3K lines). `DEFAULT_CONFIG`, `load_config()`, `save_config()`, `get_hermes_home()`, `.env` read/write, managed-mode detection. |
| `env_loader.py` | `load_hermes_dotenv()` -- loads `~/.hermes/.env` then project `.env` fallback. |
| `profiles.py` | Profile isolation: `hermes profile create/use/delete/list`. Independent `HERMES_HOME` per profile. |
| `default_soul.py` | `DEFAULT_SOUL_MD` -- persona template seeded into `SOUL.md` on first run. |

### Setup Wizard
| File | Role |
|------|------|
| `setup.py` | Interactive setup wizard (3K lines). Five sections: Model & Provider, Terminal, Agent, Platforms, Tools. Writes `config.yaml` + `.env`. |
| `tools_config.py` | `hermes tools` -- platform-aware toolset toggle UI with API key config. |
| `skills_config.py` | Per-platform skill enable/disable config in config.yaml. |
| `mcp_config.py` | `hermes mcp add/remove/list/test/configure` -- MCP server management. |
| `memory_setup.py` | `hermes memory setup/status` -- memory provider auto-detect and config UI. |
| `platforms.py` | `PLATFORMS` registry -- platform metadata shared by skills_config and tools_config. |

### Model System
| File | Role |
|------|------|
| `models.py` | Canonical model catalogs (1900 lines). OpenRouter/Copilot/Qwen/Codex catalogs, live fetch, fuzzy match. |
| `model_switch.py` | Shared `/model` logic: parse flags -> alias -> provider -> normalize -> metadata. |
| `model_normalize.py` | Per-provider model name formatting (aggregator slugs vs bare names vs dot/hyphen). |
| `codex_models.py` | Codex/OpenAI model discovery from API, local cache, and config. |
| `providers.py` | Provider identity source of truth. Merges models.dev + Hermes overlays + user config. |

### Gateway & Services
| File | Role |
|------|------|
| `gateway.py` | `hermes gateway [run|start|stop|restart|install|uninstall|setup]` -- systemd/launchd service management. |
| `cron.py` | `hermes cron list/create/edit/pause/resume/run/remove` -- scheduled jobs. |
| `webhook.py` | `hermes webhook subscribe/list/remove/test` -- persistent webhook subscriptions. |
| `pairing.py` | `hermes pairing list/approve/revoke` -- DM pairing code approval. |
| `web_server.py` | `hermes web` -- FastAPI + Vite/React dashboard with REST API. |

### UI & Display
| File | Role |
|------|------|
| `skin_engine.py` | Data-driven YAML skin/theme system. Built-in presets + `~/.hermes/skins/`. |
| `banner.py` | Welcome banner, ASCII art, skills summary, update check via Rich. |
| `colors.py` | ANSI color utilities, `Colors` enum, `NO_COLOR` support. |
| `cli_output.py` | `print_info/success/warning/error` -- shared output helpers. |
| `tips.py` | Random tip corpus shown at session start. |
| `curses_ui.py` | Curses multi-select checklists with numbered text fallback. |
| `callbacks.py` | Terminal_tool prompt callbacks bridged into prompt_toolkit event loop. |
| `clipboard.py` | Cross-platform clipboard image extraction (macOS/Win/Linux/WSL2). |

### Plugins & Skills
| File | Role |
|------|------|
| `plugins.py` | Plugin discovery & lifecycle: user/project/pip sources. `register(ctx)` hooks, `invoke_hook()`. |
| `plugins_cmd.py` | `hermes plugins install/update/remove/list` -- Git-based plugin management. |
| `skills_hub.py` | `hermes skills search/browse/inspect/install` + `/skills` slash command. |

### Diagnostics & Ops
| File | Role |
|------|------|
| `doctor.py` | `hermes doctor` -- config, dependency, connectivity diagnostics. |
| `status.py` | `hermes status` -- component health overview (provider, gateway, cron). |
| `debug.py` | `hermes debug share` -- uploads system info + logs for support. |
| `dump.py` | `hermes dump` -- plain-text setup summary (no ANSI). |
| `logs.py` | `hermes logs [-f] [errors] [--since]` -- log viewer with tail/follow/filter. |
| `backup.py` | `hermes backup/import` -- zip archive of `~/.hermes/`. |
| `uninstall.py` | `hermes uninstall` -- full removal or keep-data options. |
| `claw.py` | `hermes claw migrate/cleanup` -- OpenClaw migration with dry-run. |

## Business Flows

### 1. Subcommand Dispatch
```
hermes <cmd> -> main.py main() argparse add_subparsers -> set_defaults(func=cmd_*)
-> args.func(args) -> cmd_* inline or delegates to module (gateway.py, setup.py, etc.)
-> _apply_profile_override() intercepts --profile/-p before any module imports
```

### 2. Slash Command Registry
```
commands.py COMMAND_REGISTRY [CommandDef] -> resolve_command(name) via _COMMAND_LOOKUP
-> CLI: prompt_toolkit loop matches /command -> handler in main.py
-> Gateway: gateway/run.py reads GATEWAY_KNOWN_COMMANDS -> handler coroutines
-> Plugins: register_command() -> rebuild_lookups() refreshes all derived dicts
```

### 3. Auth Flow
```
auth.py PROVIDER_REGISTRY [ProviderConfig] -> resolve_provider(): config > OAuth > env
-> OAuth: device_code_flow() -> poll -> store auth.json (file-locked)
-> API key: env var / .env via resolve_api_key_provider_credentials()
-> runtime_provider.py merges config + auth + credential_pool for final creds
```

### 4. Config System
```
config.py DEFAULT_CONFIG + ~/.hermes/config.yaml -> load_config() merges
-> .env: env_loader.py loads ~/.hermes/.env then project .env fallback
-> setup.py wizard writes config.yaml + .env (5 interactive sections)
-> profiles.py --profile/-p -> resolve_profile_env() -> sets HERMES_HOME early
```