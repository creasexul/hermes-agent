# environments/ -- Atropos RL Training Environments

[Parent index](../AGENTS.md)

Atropos integration layer: multi-turn agent loop engine, per-rollout tool context,
abstract base environment, 12 tool-call parsers for different model families, and
4 concrete environments plus 3 eval benchmarks. Two-phase operation: Phase 1
(OpenAI server, native tool parsing) and Phase 2 (VLLM ManagedServer, client-side
parsing with exact token tracking + logprobs for RL training).

## File Map

### Core Engine

| File | Description |
|------|-------------|
| `__init__.py` | Package exports (`AgentResult`, `HermesAgentLoop`, `ToolContext`, `HermesAgentBaseEnv`); guarded by `atroposlib` availability |
| `hermes_base_env.py` | `HermesAgentBaseEnv(BaseEnv)` + `HermesAgentEnvConfig` -- abstract base: toolset resolution, Phase 1/2 dispatch, `collect_trajectory()`, wandb rollout display |
| `agent_loop.py` | `HermesAgentLoop` -- reusable multi-turn loop: `chat_completion` + tool dispatch via `handle_function_call()` in thread pool; `AgentResult` dataclass |
| `tool_context.py` | `ToolContext` -- per-rollout tool handle for reward functions: `terminal()`, `read_file()`, `write_file()`, `upload_file/dir()`, `download_file/dir()`, `web_search()`, `browser_navigate()`, generic `call_tool()`, `cleanup()` |
| `patches.py` | Legacy no-op (async safety now built into Modal backend); kept for backward-compat imports |

### Concrete Environments

| File | Description |
|------|-------------|
| `terminal_test_env/terminal_test_env.py` | `TerminalTestEnv` -- inline file-creation tasks for stack validation (3 train + 1 eval); `default.yaml` config |
| `hermes_swe_env/hermes_swe_env.py` | `HermesSweEnv` -- SWE-bench style coding tasks with Modal sandboxes; reward via test execution in ToolContext |
| `agentic_opd_env.py` | `AgenticOPDEnv` -- on-policy distillation: extracts hindsight hints from tool results, scores student tokens under enhanced teacher distribution |
| `web_research_env.py` | `WebResearchEnv` -- multi-step web research on FRAMES benchmark; composite reward (correctness + source diversity + efficiency) |

### Tool Call Parsers (`tool_call_parsers/`)

| File | Description |
|------|-------------|
| `__init__.py` | `ToolCallParser` ABC, `PARSER_REGISTRY` dict, `get_parser(name)`, `@register_parser` decorator; imports all parsers at load to trigger registration |
| `hermes_parser.py` | `<tool_call>` XML tags with JSON body (Hermes/ChatML models) |
| `mistral_parser.py` | `[TOOL_CALLS]` format (Mistral family) |
| `llama_parser.py` | JSON tool calling (Llama 3 family) |
| `qwen_parser.py` | Qwen tool calling format |
| `qwen3_coder_parser.py` | Qwen3 Coder format |
| `deepseek_v3_parser.py` | DeepSeek V3 format |
| `deepseek_v3_1_parser.py` | DeepSeek V3.1 format |
| `kimi_k2_parser.py` | Kimi K2 format |
| `longcat_parser.py` | Longcat format |
| `glm45_parser.py` | GLM-4.5 format |
| `glm47_parser.py` | GLM-4.7 format |

### Benchmarks (`benchmarks/`)

| File | Description |
|------|-------------|
| `terminalbench_2/terminalbench2_env.py` | `TerminalBench2EvalEnv` -- 89 terminal tasks, per-task Docker images on Modal, binary pass/fail via test.sh; `default.yaml` config |
| `tblite/tblite_env.py` | `TBLiteEvalEnv(TerminalBench2EvalEnv)` -- 100 difficulty-calibrated tasks (easy/med/hard/extreme); fast TB2 proxy; `default.yaml`, `local.yaml`, `local_vllm.yaml` |
| `yc_bench/yc_bench_env.py` | `YCBenchEvalEnv` -- long-horizon strategic benchmark: agent as startup CEO over simulated 1-3 years via CLI against SQLite sim; `default.yaml` config |

## Business Flows

### Inheritance Chain
```
atroposlib.BaseEnv (server mgmt, worker scheduling, wandb, CLI serve/process/evaluate)
  -> HermesAgentBaseEnv (toolset resolution, agent loop, ToolContext, Phase 1/2)
       -> TerminalTestEnv        (inline tasks, stack validation)
       -> HermesSweEnv           (SWE-bench, Modal sandboxes)
       -> AgenticOPDEnv          (on-policy distillation, dense token-level signal)
       -> WebResearchEnv         (web research, FRAMES benchmark)
       -> TerminalBench2EvalEnv  (TB2 eval-only, 89 tasks)
            -> TBLiteEvalEnv     (lighter 100-task TB2 proxy)
       -> YCBenchEvalEnv         (long-horizon strategic eval)
```
Subclasses implement: `setup()`, `get_next_item()`, `format_prompt()`, `compute_reward(item, result, ctx)`, `evaluate()`.

### Benchmark Evaluation Flow
```
CLI: python <env>.py evaluate --config default.yaml
  -> setup() loads dataset from HuggingFace
  -> evaluate() iterates tasks (asyncio.Semaphore concurrency)
       -> rollout_and_score_eval(task) per task:
            -> provisions sandbox (Modal Docker image or local)
            -> HermesAgentLoop.run(messages) -- multi-turn tool calling
            -> uploads test suite, runs test.sh in same sandbox
            -> ToolContext reads results -> binary/composite reward
            -> ToolContext.cleanup() releases sandbox
  -> aggregates per-task / per-category / overall scores
  -> evaluate_log() writes JSON + samples.jsonl + wandb
```

### Parser Discovery Flow
```
config.tool_call_parser = "hermes" (set in YAML)
  -> HermesAgentBaseEnv.__init__ sets self.server.tool_parser
  -> Phase 2: ManagedServer ToolCallTranslator uses parser for raw text <-> tool_calls
  -> Fallback: agent_loop.py calls get_parser("hermes") if <tool_call> tags found in content
  -> PARSER_REGISTRY populated by @register_parser decorators at import time
  -> parser.parse(raw_text) -> (content, List[ChatCompletionMessageToolCall])
```
