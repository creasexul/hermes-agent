#!/usr/bin/env python3
"""
Workflow Tool — Declarative DAG-based Multi-Agent Orchestration

Executes YAML-defined workflows as Directed Acyclic Graphs (DAGs).
Each node can be an agent task, a shell command, or a control-flow construct.

Inspired by Archon's DAG executor, adapted for Hermes Agent's architecture.

Key features:
  - YAML declarative workflow definitions
  - DAG topological sort with parallel execution within layers
  - 6 node types: agent, prompt, bash, loop, gate, cancel
  - Variable interpolation ($node_id.output, $ARGS, etc.)
  - Conditional branching (when expressions)
  - Structured output enforcement (output_format JSON schema)
  - State persistence + resume from failure
  - Retry with exponential backoff
"""

import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Schema & Data Structures
# =============================================================================

class NodeType(Enum):
    AGENT = "agent"             # Spawn a subagent with goal/context
    PROMPT = "prompt"           # Inline prompt sent to parent agent's LLM
    BASH = "bash"               # Shell command execution
    LOOP = "loop"               # Repeat until condition or max iterations
    GATE = "gate"               # Pause for human approval
    CANCEL = "cancel"           # Abort workflow with reason
    CLAUDE_CODE = "claude_code" # Claude Code CLI for coding tasks


class NodeStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    WAITING_APPROVAL = "waiting_approval"


class WorkflowStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"  # Waiting for gate approval


@dataclass
class RetryConfig:
    max_attempts: int = 3
    delay_seconds: float = 5.0
    backoff_multiplier: float = 2.0
    on_error: str = "all"  # "all", "transient"


@dataclass
class NodeDef:
    """Parsed node definition from YAML."""
    id: str
    node_type: NodeType
    depends_on: List[str] = field(default_factory=list)
    when: Optional[str] = None  # Condition expression
    retry: Optional[RetryConfig] = None
    timeout: int = 300  # seconds
    toolsets: Optional[List[str]] = None  # For agent nodes

    # Type-specific fields
    goal: Optional[str] = None          # agent
    context: Optional[str] = None       # agent
    acp_command: Optional[str] = None   # agent — ACP external agent (e.g. "claude")
    acp_args: Optional[List[str]] = None # agent — ACP args (e.g. ["--acp", "--stdio"])
    prompt_text: Optional[str] = None   # prompt
    bash_cmd: Optional[str] = None      # bash
    loop_prompt: Optional[str] = None   # loop
    loop_until: Optional[str] = None    # loop — completion signal
    loop_max: int = 10                  # loop — max iterations
    cancel_reason: Optional[str] = None # cancel
    gate_message: Optional[str] = None  # gate

    # claude_code type-specific fields
    claude_code_task: Optional[str] = None    # claude_code — task prompt
    workdir: Optional[str] = None             # claude_code/bash — working directory
    allowed_tools: Optional[List[str]] = None # claude_code — --allowedTools
    max_turns: Optional[int] = None           # claude_code — --max-turns (default 20)
    claude_model: Optional[str] = None        # claude_code — --model (sonnet/opus/haiku)
    max_budget_usd: Optional[float] = None    # claude_code — --max-budget-usd

    output_format: Optional[dict] = None  # JSON schema for structured output


@dataclass
class NodeResult:
    """Result of executing a single node."""
    node_id: str
    status: NodeStatus
    output: str = ""
    error: Optional[str] = None
    duration_seconds: float = 0.0
    attempts: int = 1
    parsed_output: Optional[dict] = None  # If output_format was specified


@dataclass
class WorkflowRun:
    """State of a workflow execution."""
    run_id: str
    workflow_name: str
    status: WorkflowStatus
    nodes: Dict[str, NodeDef]
    node_results: Dict[str, NodeResult] = field(default_factory=dict)
    start_time: float = 0.0
    end_time: float = 0.0
    arguments: str = ""
    error: Optional[str] = None
    retrospect: Optional[dict] = None  # Post-run analysis (practicality + quality)


# =============================================================================
# YAML Parsing
# =============================================================================

@dataclass
class WorkflowOptions:
    """Runtime options parsed from YAML 'options' block."""
    scheduler: bool = False
    reviewer: bool = False
    reviewer_threshold: int = 7  # min score 0-10 to pass
    max_rework_attempts: int = 1  # retry failed reviews


def parse_workflow(yaml_text: str, arguments: str = "") -> Tuple[str, List[NodeDef], WorkflowOptions]:
    """Parse a YAML workflow definition into node definitions.

    Returns (workflow_name, list_of_NodeDef, WorkflowOptions).
    """
    try:
        import yaml
    except ImportError:
        # PyYAML should be available in Hermes venv
        raise ImportError("PyYAML is required for workflow execution. Install with: pip install pyyaml")

    doc = yaml.safe_load(yaml_text)
    if not isinstance(doc, dict):
        raise ValueError("Workflow YAML must be a mapping at the top level")

    name = doc.get("name", "unnamed-workflow")
    raw_nodes = doc.get("nodes", [])
    if not raw_nodes:
        raise ValueError("Workflow must define at least one node in 'nodes'")

    # Parse options block
    opts_raw = doc.get("options", {})
    options = WorkflowOptions(
        scheduler=opts_raw.get("scheduler", False),
        reviewer=opts_raw.get("reviewer", False),
        reviewer_threshold=opts_raw.get("reviewer_threshold", 7),
        max_rework_attempts=opts_raw.get("max_rework_attempts", 1),
    )

    nodes = []
    seen_ids = set()
    for raw in raw_nodes:
        node_id = raw.get("id")
        if not node_id:
            raise ValueError(f"Every node must have an 'id'. Got: {raw}")
        if node_id in seen_ids:
            raise ValueError(f"Duplicate node id: '{node_id}'")
        seen_ids.add(node_id)

        # Determine node type from fields
        node_type = _infer_node_type(raw)

        # Parse retry config
        retry = None
        if "retry" in raw:
            rc = raw["retry"]
            retry = RetryConfig(
                max_attempts=rc.get("max_attempts", 3),
                delay_seconds=rc.get("delay_seconds", 5.0),
                backoff_multiplier=rc.get("backoff_multiplier", 2.0),
                on_error=rc.get("on_error", "all"),
            )

        node = NodeDef(
            id=node_id,
            node_type=node_type,
            depends_on=_ensure_list(raw.get("depends_on", [])),
            when=raw.get("when"),
            retry=retry,
            timeout=raw.get("timeout", 300),
            toolsets=raw.get("toolsets"),
            goal=raw.get("goal"),
            context=raw.get("context"),
            acp_command=raw.get("acp_command"),
            acp_args=raw.get("acp_args"),
            prompt_text=raw.get("prompt"),
            bash_cmd=raw.get("bash"),
            loop_prompt=raw.get("loop", {}).get("prompt") if isinstance(raw.get("loop"), dict) else None,
            loop_until=raw.get("loop", {}).get("until") if isinstance(raw.get("loop"), dict) else None,
            loop_max=raw.get("loop", {}).get("max_iterations", 10) if isinstance(raw.get("loop"), dict) else 10,
            cancel_reason=raw.get("cancel"),
            gate_message=raw.get("gate"),
            claude_code_task=raw.get("claude_code"),
            workdir=raw.get("workdir"),
            allowed_tools=raw.get("allowed_tools"),
            max_turns=raw.get("max_turns"),
            claude_model=raw.get("model"),
            max_budget_usd=raw.get("max_budget_usd"),
            output_format=raw.get("output_format"),
        )
        nodes.append(node)

    # Validate DAG — check for missing dependencies
    for node in nodes:
        for dep in node.depends_on:
            if dep not in seen_ids:
                raise ValueError(f"Node '{node.id}' depends on unknown node '{dep}'")

    # Check for cycles
    _check_cycles(nodes)

    return name, nodes, options


def _infer_node_type(raw: dict) -> NodeType:
    """Infer node type from the fields present."""
    if "claude_code" in raw:
        return NodeType.CLAUDE_CODE
    if "goal" in raw:
        return NodeType.AGENT
    if "bash" in raw:
        return NodeType.BASH
    if "loop" in raw:
        return NodeType.LOOP
    if "gate" in raw:
        return NodeType.GATE
    if "cancel" in raw:
        return NodeType.CANCEL
    if "prompt" in raw:
        return NodeType.PROMPT
    raise ValueError(f"Cannot infer node type for node '{raw.get('id', '?')}'. "
                     f"Must have one of: goal, prompt, bash, loop, gate, cancel")


def _ensure_list(val) -> list:
    if val is None:
        return []
    if isinstance(val, str):
        return [val]
    return list(val)


def _check_cycles(nodes: List[NodeDef]):
    """Detect cycles using DFS."""
    adjacency = {n.id: set(n.depends_on) for n in nodes}
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {n.id: WHITE for n in nodes}

    def dfs(node_id):
        color[node_id] = GRAY
        for dep in adjacency.get(node_id, []):
            if color[dep] == GRAY:
                raise ValueError(f"Cycle detected involving node '{dep}'")
            if color[dep] == WHITE:
                dfs(dep)
        color[node_id] = BLACK

    for n in nodes:
        if color[n.id] == WHITE:
            dfs(n.id)


# =============================================================================
# DAG Topological Sort (Kahn's Algorithm)
# =============================================================================

def build_topological_layers(nodes: List[NodeDef]) -> List[List[NodeDef]]:
    """Sort nodes into layers using Kahn's algorithm.

    Nodes in the same layer have no dependencies on each other
    and can be executed in parallel.
    """
    node_map = {n.id: n for n in nodes}
    in_degree = {n.id: len(n.depends_on) for n in nodes}
    reverse_deps = {n.id: [] for n in nodes}
    for n in nodes:
        for dep in n.depends_on:
            reverse_deps[dep].append(n.id)

    layers = []
    available = [nid for nid, deg in in_degree.items() if deg == 0]

    while available:
        layer = [node_map[nid] for nid in sorted(available)]
        layers.append(layer)

        next_available = []
        for nid in available:
            for child_id in reverse_deps[nid]:
                in_degree[child_id] -= 1
                if in_degree[child_id] == 0:
                    next_available.append(child_id)
        available = next_available

    scheduled = sum(len(l) for l in layers)
    if scheduled != len(nodes):
        raise ValueError(
            f"DAG scheduling error: {scheduled}/{len(nodes)} nodes scheduled. "
            f"Possible hidden cycle or missing dependency."
        )
    return layers


# =============================================================================
# Variable Interpolation
# =============================================================================

# Pattern: $node_id.output or $node_id.output.field
_VAR_PATTERN = re.compile(r'\$([A-Za-z_][A-Za-z0-9_]*)\.output(?:\.([A-Za-z_][A-Za-z0-9_.]*))?')
# Pattern: $ARGS, $WORKFLOW_NAME, $RUN_ID etc.
_GLOBAL_VAR_PATTERN = re.compile(r'\$([A-Z_]+)')

_GLOBAL_VARS = {"ARGS", "WORKFLOW_NAME", "RUN_ID"}


def interpolate(text: str, node_outputs: Dict[str, NodeResult],
                global_vars: Dict[str, str]) -> str:
    """Replace $node_id.output and $GLOBAL references in text."""
    if not text:
        return text

    def _replace_node_var(match):
        node_id = match.group(1)
        field_path = match.group(2)
        result = node_outputs.get(node_id)
        if result is None:
            return match.group(0)  # Leave as-is if node hasn't run

        if field_path and result.parsed_output:
            # Drill into parsed JSON output
            val = result.parsed_output
            for key in field_path.split("."):
                if isinstance(val, dict):
                    val = val.get(key, "")
                else:
                    val = ""
                    break
            return str(val) if val is not None else ""
        return result.output

    def _replace_global_var(match):
        var_name = match.group(1)
        if var_name in _GLOBAL_VARS:
            return global_vars.get(var_name, match.group(0))
        return match.group(0)

    text = _VAR_PATTERN.sub(_replace_node_var, text)
    text = _GLOBAL_VAR_PATTERN.sub(_replace_global_var, text)
    return text


# =============================================================================
# Condition Evaluation
# =============================================================================

def evaluate_condition(when_expr: str, node_outputs: Dict[str, NodeResult]) -> bool:
    """Evaluate a 'when' condition expression.

    Supports:
      - $node.output.field == 'value'
      - $node.output.field != 'value'
      - $node.output contains 'substring'
      - $node.status == 'completed'
      - true / false literals
    """
    if not when_expr:
        return True

    expr = when_expr.strip()

    # Literal booleans
    if expr.lower() == "true":
        return True
    if expr.lower() == "false":
        return False

    # Status check: $node.status == 'completed'
    status_match = re.match(
        r'\$([A-Za-z_][A-Za-z0-9_]*)\.status\s*(==|!=)\s*[\'"](\w+)[\'"]',
        expr
    )
    if status_match:
        node_id = status_match.group(1)
        op = status_match.group(2)
        expected = status_match.group(3)
        result = node_outputs.get(node_id)
        actual = result.status.value if result else "pending"
        if op == "==":
            return actual == expected
        return actual != expected

    # Output field comparison: $node.output.field == 'value'
    field_match = re.match(
        r'\$([A-Za-z_][A-Za-z0-9_]*)\.output(?:\.([A-Za-z_][A-Za-z0-9_.]*))?\s*(==|!=)\s*[\'"]([^\'"]*)[\'"]',
        expr
    )
    if field_match:
        node_id = field_match.group(1)
        field_path = field_match.group(2)
        op = field_match.group(3)
        expected = field_match.group(4)
        result = node_outputs.get(node_id)
        if result is None:
            return False
        actual = result.output
        if field_path and result.parsed_output:
            val = result.parsed_output
            for key in field_path.split("."):
                if isinstance(val, dict):
                    val = val.get(key, "")
                else:
                    val = ""
                    break
            actual = str(val) if val is not None else ""
        if op == "==":
            return actual.strip() == expected.strip()
        return actual.strip() != expected.strip()

    # Contains check: $node.output contains 'substring'
    contains_match = re.match(
        r'\$([A-Za-z_][A-Za-z0-9_]*)\.output\s+contains\s+[\'"]([^\'"]*)[\'"]',
        expr
    )
    if contains_match:
        node_id = contains_match.group(1)
        substring = contains_match.group(2)
        result = node_outputs.get(node_id)
        if result is None:
            return False
        return substring in result.output

    logger.warning("Cannot evaluate condition: %s — defaulting to True", expr)
    return True


# =============================================================================
# State Persistence
# =============================================================================

def _get_workflow_state_dir() -> Path:
    """Get directory for workflow state files."""
    try:
        from hermes_constants import get_hermes_home
        base = Path(get_hermes_home())
    except ImportError:
        base = Path.home() / ".hermes"
    state_dir = base / "workflows"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def save_workflow_state(run: WorkflowRun):
    """Persist workflow state to disk for resume capability."""
    state_dir = _get_workflow_state_dir()
    state = {
        "run_id": run.run_id,
        "workflow_name": run.workflow_name,
        "status": run.status.value,
        "start_time": run.start_time,
        "end_time": run.end_time,
        "arguments": run.arguments,
        "error": run.error,
        "node_results": {
            nid: {
                "node_id": nr.node_id,
                "status": nr.status.value,
                "output": nr.output[:10000],  # Cap output size
                "error": nr.error,
                "duration_seconds": nr.duration_seconds,
                "attempts": nr.attempts,
                "parsed_output": nr.parsed_output,
            }
            for nid, nr in run.node_results.items()
        },
    }
    state_file = state_dir / f"{run.run_id}.json"
    state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2))
    logger.debug("Saved workflow state: %s", state_file)


def load_workflow_state(run_id: str) -> Optional[dict]:
    """Load a previous workflow run state."""
    state_dir = _get_workflow_state_dir()
    state_file = state_dir / f"{run_id}.json"
    if state_file.exists():
        return json.loads(state_file.read_text())
    return None


def find_resumable_run(workflow_name: str) -> Optional[dict]:
    """Find the most recent failed/paused run for a workflow."""
    state_dir = _get_workflow_state_dir()
    best = None
    best_time = 0.0
    for f in state_dir.glob("*.json"):
        try:
            state = json.loads(f.read_text())
            if (state.get("workflow_name") == workflow_name
                    and state.get("status") in ("failed", "paused")
                    and state.get("start_time", 0) > best_time):
                best = state
                best_time = state["start_time"]
        except Exception:
            continue
    return best


def list_workflow_runs(limit: int = 10) -> List[dict]:
    """List recent workflow runs."""
    state_dir = _get_workflow_state_dir()
    runs = []
    for f in sorted(state_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        if len(runs) >= limit:
            break
        try:
            state = json.loads(f.read_text())
            runs.append({
                "run_id": state.get("run_id"),
                "workflow_name": state.get("workflow_name"),
                "status": state.get("status"),
                "start_time": state.get("start_time"),
                "end_time": state.get("end_time"),
                "nodes_completed": sum(
                    1 for nr in state.get("node_results", {}).values()
                    if nr.get("status") == "completed"
                ),
                "error": state.get("error"),
            })
        except Exception:
            continue
    return runs


# =============================================================================
# Structured Output Parsing
# =============================================================================

def try_parse_structured_output(output: str, schema: Optional[dict]) -> Optional[dict]:
    """Attempt to extract JSON from agent output based on schema.

    Tries:
      1. Direct JSON parse
      2. Extract JSON from markdown code blocks
      3. Extract JSON object from text
    """
    if not schema or not output:
        return None

    text = output.strip()

    # 1. Direct parse
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # 2. Markdown code block
    code_block = re.search(r'```(?:json)?\s*\n(.*?)\n```', text, re.DOTALL)
    if code_block:
        try:
            parsed = json.loads(code_block.group(1).strip())
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    # 3. First JSON object in text
    brace_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text)
    if brace_match:
        try:
            parsed = json.loads(brace_match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# =============================================================================
# Node Executors
# =============================================================================

def _execute_agent_node(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
    parent_agent=None,
) -> NodeResult:
    """Execute an agent node by spawning a subagent via delegate_task."""
    start = time.monotonic()

    goal = interpolate(node.goal or "", node_outputs, global_vars)
    context = interpolate(node.context or "", node_outputs, global_vars)

    # Add output format instructions to context if specified
    if node.output_format:
        schema_str = json.dumps(node.output_format, indent=2)
        context += (
            f"\n\n## Required Output Format\n"
            f"You MUST respond with a JSON object matching this schema:\n"
            f"```json\n{schema_str}\n```\n"
            f"Return ONLY the JSON, no additional text."
        )

    if parent_agent is None:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error="No parent agent available for agent nodes",
            duration_seconds=time.monotonic() - start,
        )

    try:
        from tools.delegate_tool import delegate_task as _delegate_task
        result_json = _delegate_task(
            goal=goal,
            context=context,
            toolsets=node.toolsets,
            max_iterations=50,
            parent_agent=parent_agent,
            acp_command=node.acp_command,
            acp_args=node.acp_args,
        )
        result_data = json.loads(result_json)
        results = result_data.get("results", [])
        if results:
            r = results[0]
            output = r.get("summary", "")
            status = r.get("status", "failed")
            if status == "completed":
                parsed = try_parse_structured_output(output, node.output_format)
                return NodeResult(
                    node_id=node.id, status=NodeStatus.COMPLETED,
                    output=output, parsed_output=parsed,
                    duration_seconds=time.monotonic() - start,
                )
            else:
                return NodeResult(
                    node_id=node.id, status=NodeStatus.FAILED,
                    output=output, error=r.get("error", "Subagent failed"),
                    duration_seconds=time.monotonic() - start,
                )
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error="No results from delegate_task",
            duration_seconds=time.monotonic() - start,
        )
    except Exception as e:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=f"Agent execution error: {e}",
            duration_seconds=time.monotonic() - start,
        )


def _execute_prompt_node(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
    parent_agent=None,
) -> NodeResult:
    """Execute a prompt node — send inline text to an LLM via subagent."""
    start = time.monotonic()
    prompt = interpolate(node.prompt_text or "", node_outputs, global_vars)

    if parent_agent is None:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error="No parent agent for prompt node",
            duration_seconds=time.monotonic() - start,
        )

    try:
        from tools.delegate_tool import delegate_task as _delegate_task
        goal = prompt
        context = None
        if node.output_format:
            context = (
                f"Respond with JSON matching this schema:\n"
                f"```json\n{json.dumps(node.output_format, indent=2)}\n```"
            )
        result_json = _delegate_task(
            goal=goal, context=context,
            toolsets=node.toolsets or ["terminal", "file"],
            max_iterations=30,
            parent_agent=parent_agent,
            acp_command=node.acp_command,
            acp_args=node.acp_args,
        )
        result_data = json.loads(result_json)
        results = result_data.get("results", [])
        if results and results[0].get("status") == "completed":
            output = results[0].get("summary", "")
            parsed = try_parse_structured_output(output, node.output_format)
            return NodeResult(
                node_id=node.id, status=NodeStatus.COMPLETED,
                output=output, parsed_output=parsed,
                duration_seconds=time.monotonic() - start,
            )
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=results[0].get("error", "Prompt execution failed") if results else "No results",
            duration_seconds=time.monotonic() - start,
        )
    except Exception as e:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=str(e), duration_seconds=time.monotonic() - start,
        )


def _execute_bash_node(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
    task_id: Optional[str] = None,
) -> NodeResult:
    """Execute a bash command node."""
    start = time.monotonic()
    cmd = interpolate(node.bash_cmd or "", node_outputs, global_vars)

    # Shell-escape node outputs in bash commands to prevent injection
    for nid, nr in node_outputs.items():
        safe_output = nr.output.replace("'", "'\\''") if nr.output else ""
        cmd = cmd.replace(f"${nid}.output", safe_output)

    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=node.timeout, cwd=os.path.expanduser("~"),
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            error_msg = result.stderr.strip() or f"Exit code {result.returncode}"
            return NodeResult(
                node_id=node.id, status=NodeStatus.FAILED,
                output=output, error=error_msg,
                duration_seconds=time.monotonic() - start,
            )
        parsed = try_parse_structured_output(output, node.output_format)
        return NodeResult(
            node_id=node.id, status=NodeStatus.COMPLETED,
            output=output, parsed_output=parsed,
            duration_seconds=time.monotonic() - start,
        )
    except subprocess.TimeoutExpired:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=f"Command timed out after {node.timeout}s",
            duration_seconds=time.monotonic() - start,
        )
    except Exception as e:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=str(e), duration_seconds=time.monotonic() - start,
        )


def _execute_claude_code_node(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
    task_id: Optional[str] = None,
) -> NodeResult:
    """Execute a claude_code node using Claude Code CLI (claude -p).

    Runs Claude Code in non-interactive print mode with JSON output, injecting
    context from prior nodes via --append-system-prompt.
    """
    start = time.monotonic()
    timeout = node.timeout if node.timeout and node.timeout != 300 else 600
    max_turns = node.max_turns if node.max_turns is not None else 20

    # Interpolate the task prompt
    task = interpolate(node.claude_code_task or "", node_outputs, global_vars)

    # Build context string from depends_on nodes (injected via system prompt, not task)
    context_str = ""
    if node.context:
        context_str = interpolate(node.context, node_outputs, global_vars)
    elif node.depends_on:
        parts = []
        for dep_id in node.depends_on:
            if dep_id in node_outputs and node_outputs[dep_id].output:
                parts.append(f"[{dep_id}]:\n{node_outputs[dep_id].output}")
        if parts:
            context_str = "\n\n".join(parts)

    # Build the claude command
    cmd_parts = [
        "claude", "-p", shlex.quote(task),
        "--output-format", "json",
        "--dangerously-skip-permissions",
        "--no-session-persistence",
        "--max-turns", str(max_turns),
    ]

    if node.claude_model:
        cmd_parts += ["--model", shlex.quote(node.claude_model)]

    if node.allowed_tools:
        tools_str = ",".join(node.allowed_tools)
        cmd_parts += ["--allowedTools", shlex.quote(tools_str)]

    if node.max_budget_usd is not None:
        cmd_parts += ["--max-budget-usd", str(node.max_budget_usd)]

    if context_str:
        cmd_parts += ["--append-system-prompt", shlex.quote(context_str)]

    cmd = " ".join(cmd_parts)

    # Determine working directory
    cwd = os.path.expanduser(node.workdir) if node.workdir else os.path.expanduser("~")

    try:
        proc = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd,
        )
        raw_output = proc.stdout.strip()

        if proc.returncode != 0 and not raw_output:
            error_msg = proc.stderr.strip() or f"Exit code {proc.returncode}"
            return NodeResult(
                node_id=node.id, status=NodeStatus.FAILED,
                error=error_msg, duration_seconds=time.monotonic() - start,
            )

        # Parse JSON output from Claude Code
        try:
            data = json.loads(raw_output)
        except (json.JSONDecodeError, ValueError):
            # Non-JSON output — treat as plain text success (shouldn't happen with --output-format json)
            return NodeResult(
                node_id=node.id, status=NodeStatus.COMPLETED,
                output=raw_output,
                duration_seconds=time.monotonic() - start,
            )

        subtype = data.get("subtype", "success")
        result_text = data.get("result", raw_output)
        total_cost = data.get("total_cost_usd", 0.0) or 0.0
        num_turns = data.get("num_turns", 0)

        # Append execution metadata
        output = f"{result_text}\n---\n[Claude Code: {num_turns} turns, ${total_cost:.4f}]"

        if subtype != "success":
            return NodeResult(
                node_id=node.id, status=NodeStatus.FAILED,
                output=output,
                error=f"Claude Code ended with subtype={subtype!r}",
                duration_seconds=time.monotonic() - start,
            )

        parsed = try_parse_structured_output(result_text, node.output_format)
        return NodeResult(
            node_id=node.id, status=NodeStatus.COMPLETED,
            output=output, parsed_output=parsed,
            duration_seconds=time.monotonic() - start,
        )

    except subprocess.TimeoutExpired:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=f"Claude Code timed out after {timeout}s",
            duration_seconds=time.monotonic() - start,
        )
    except Exception as e:
        return NodeResult(
            node_id=node.id, status=NodeStatus.FAILED,
            error=str(e), duration_seconds=time.monotonic() - start,
        )


def _execute_loop_node(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
    parent_agent=None,
) -> NodeResult:
    """Execute a loop node — repeat prompt until condition or max iterations."""
    start = time.monotonic()
    accumulated_output = []

    for iteration in range(1, node.loop_max + 1):
        iter_vars = {**global_vars, "LOOP_ITERATION": str(iteration)}
        prompt = interpolate(node.loop_prompt or "", node_outputs, iter_vars)
        prompt += f"\n\n[Iteration {iteration}/{node.loop_max}]"

        if parent_agent is None:
            return NodeResult(
                node_id=node.id, status=NodeStatus.FAILED,
                error="No parent agent for loop node",
                duration_seconds=time.monotonic() - start,
            )

        try:
            from tools.delegate_tool import delegate_task as _delegate_task
            result_json = _delegate_task(
                goal=prompt,
                context="\n---\n".join(accumulated_output[-3:]) if accumulated_output else None,
                toolsets=node.toolsets or ["terminal", "file"],
                max_iterations=30,
                parent_agent=parent_agent,
                acp_command=node.acp_command,
                acp_args=node.acp_args,
            )
            result_data = json.loads(result_json)
            results = result_data.get("results", [])
            if results:
                output = results[0].get("summary", "")
                accumulated_output.append(f"[Iteration {iteration}]\n{output}")

                # Check completion signal
                if node.loop_until and node.loop_until.lower() in output.lower():
                    return NodeResult(
                        node_id=node.id, status=NodeStatus.COMPLETED,
                        output="\n\n".join(accumulated_output),
                        duration_seconds=time.monotonic() - start,
                        attempts=iteration,
                    )
        except Exception as e:
            accumulated_output.append(f"[Iteration {iteration}] ERROR: {e}")

    # Reached max iterations
    return NodeResult(
        node_id=node.id, status=NodeStatus.COMPLETED,
        output="\n\n".join(accumulated_output),
        error=f"Reached max iterations ({node.loop_max}) without completion signal",
        duration_seconds=time.monotonic() - start,
        attempts=node.loop_max,
    )


def _execute_gate_node(node: NodeDef) -> NodeResult:
    """Gate node — signals workflow to pause for approval."""
    return NodeResult(
        node_id=node.id, status=NodeStatus.WAITING_APPROVAL,
        output=node.gate_message or "Workflow paused, awaiting approval.",
    )


def _execute_cancel_node(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
) -> NodeResult:
    """Cancel node — abort workflow with reason."""
    reason = interpolate(node.cancel_reason or "Workflow cancelled", node_outputs, global_vars)
    return NodeResult(
        node_id=node.id, status=NodeStatus.CANCELLED,
        output=reason,
    )


# =============================================================================
# Error Classification & Retry
# =============================================================================

def _classify_error(error_str: str) -> str:
    """Classify error as FATAL, TRANSIENT, or UNKNOWN."""
    if not error_str:
        return "UNKNOWN"
    lower = error_str.lower()
    # Fatal: auth, permission, invalid model
    if any(kw in lower for kw in ["401", "403", "authentication", "permission", "invalid api"]):
        return "FATAL"
    # Transient: rate limit, timeout, connection
    if any(kw in lower for kw in ["429", "timeout", "timed out", "connection", "rate limit", "overloaded"]):
        return "TRANSIENT"
    return "UNKNOWN"


def _should_retry(error_str: str, retry_cfg: RetryConfig, attempt: int) -> bool:
    """Determine if a failed node should be retried."""
    if attempt >= retry_cfg.max_attempts:
        return False
    error_class = _classify_error(error_str)
    if error_class == "FATAL":
        return False
    if retry_cfg.on_error == "transient" and error_class != "TRANSIENT":
        return False
    return True


# =============================================================================
# DAG Executor
# =============================================================================

def execute_workflow(
    yaml_text: str,
    arguments: str = "",
    parent_agent=None,
    task_id: Optional[str] = None,
    resume_run_id: Optional[str] = None,
    progress_callback=None,
    template_name: Optional[str] = None,
) -> WorkflowRun:
    """Execute a workflow defined in YAML.

    Args:
        yaml_text: YAML workflow definition
        arguments: User arguments ($ARGS variable)
        parent_agent: The calling AIAgent instance (for subagent spawning)
        task_id: Task ID for terminal session isolation
        resume_run_id: Run ID to resume from (loads completed node results)
        progress_callback: Optional fn(event_type, node_id, message) for progress
        template_name: Template name if executing from a template (for stats)

    Returns:
        WorkflowRun with results
    """
    workflow_name, nodes, options = parse_workflow(yaml_text, arguments)
    node_map = {n.id: n for n in nodes}

    # Initialize Scheduler and Reviewer based on options
    scheduler = None
    reviewer = None
    if options.scheduler and parent_agent:
        try:
            from tools.workflow_planner import WorkflowScheduler
            scheduler = WorkflowScheduler(arguments, yaml_text, parent_agent)
            _notify(progress_callback, "scheduler_init", "",
                    "Scheduler active — monitoring execution")
        except ImportError:
            logger.warning("workflow_planner not available, scheduler disabled")

    if options.reviewer and parent_agent:
        try:
            from tools.workflow_planner import WorkflowReviewer
            reviewer = WorkflowReviewer(parent_agent, options.reviewer_threshold)
            _notify(progress_callback, "reviewer_init", "",
                    f"Reviewer active — threshold {options.reviewer_threshold}/10")
        except ImportError:
            logger.warning("workflow_planner not available, reviewer disabled")

    # Initialize run
    run = WorkflowRun(
        run_id=resume_run_id or str(uuid.uuid4())[:8],
        workflow_name=workflow_name,
        status=WorkflowStatus.RUNNING,
        nodes=node_map,
        start_time=time.time(),
        arguments=arguments,
    )

    global_vars = {
        "ARGS": arguments,
        "WORKFLOW_NAME": workflow_name,
        "RUN_ID": run.run_id,
    }

    # Load previous results if resuming
    if resume_run_id:
        prev_state = load_workflow_state(resume_run_id)
        if prev_state:
            for nid, nr_data in prev_state.get("node_results", {}).items():
                if nr_data.get("status") == "completed":
                    run.node_results[nid] = NodeResult(
                        node_id=nid,
                        status=NodeStatus.COMPLETED,
                        output=nr_data.get("output", ""),
                        parsed_output=nr_data.get("parsed_output"),
                        duration_seconds=nr_data.get("duration_seconds", 0),
                    )
            _notify(progress_callback, "workflow_resumed", "",
                    f"Resumed from {len(run.node_results)} completed nodes")

    # Build topological layers
    layers = build_topological_layers(nodes)
    total_nodes = len(nodes)
    completed_count = len(run.node_results)

    # Track review scores for template optimization
    node_scores: Dict[str, dict] = {}

    _notify(progress_callback, "workflow_started", "",
            f"Workflow '{workflow_name}' started — {total_nodes} nodes in {len(layers)} layers")

    try:
        for layer_idx, layer in enumerate(layers):
            layer_nodes = [n for n in layer if n.id not in run.node_results]
            if not layer_nodes:
                continue  # All nodes in this layer already completed (resume)

            _notify(progress_callback, "layer_started", "",
                    f"Layer {layer_idx + 1}/{len(layers)}: {[n.id for n in layer_nodes]}")

            if len(layer_nodes) == 1:
                # Single node — execute directly
                result = _execute_node_with_retry(
                    layer_nodes[0], run.node_results, global_vars,
                    parent_agent, task_id, progress_callback
                )

                # === Reviewer check (single node) ===
                if (result.status == NodeStatus.COMPLETED and reviewer
                        and layer_nodes[0].node_type in (NodeType.AGENT, NodeType.PROMPT, NodeType.CLAUDE_CODE)):
                    result, review_data = _review_and_maybe_rework(
                        reviewer, layer_nodes[0], result,
                        run.node_results, global_vars,
                        parent_agent, task_id, progress_callback,
                        arguments, options.max_rework_attempts,
                    )
                    node_scores[result.node_id] = review_data

                run.node_results[result.node_id] = result
                save_workflow_state(run)

                if result.status == NodeStatus.WAITING_APPROVAL:
                    run.status = WorkflowStatus.PAUSED
                    run.end_time = time.time()
                    save_workflow_state(run)
                    return run

                if result.status == NodeStatus.CANCELLED:
                    run.status = WorkflowStatus.CANCELLED
                    run.error = result.output
                    run.end_time = time.time()
                    save_workflow_state(run)
                    return run

                if result.status == NodeStatus.FAILED:
                    # Check if any downstream node is critical
                    run.status = WorkflowStatus.FAILED
                    run.error = f"Node '{result.node_id}' failed: {result.error}"
                    run.end_time = time.time()
                    save_workflow_state(run)
                    return run

                completed_count += 1
            else:
                # Multiple nodes — parallel execution
                with ThreadPoolExecutor(max_workers=min(len(layer_nodes), 3)) as pool:
                    futures = {
                        pool.submit(
                            _execute_node_with_retry,
                            node, run.node_results, global_vars,
                            parent_agent, task_id, progress_callback
                        ): node
                        for node in layer_nodes
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        node_def = futures[future]

                        # === Reviewer check (parallel node) ===
                        if (result.status == NodeStatus.COMPLETED and reviewer
                                and node_def.node_type in (NodeType.AGENT, NodeType.PROMPT, NodeType.CLAUDE_CODE)):
                            result, review_data = _review_and_maybe_rework(
                                reviewer, node_def, result,
                                run.node_results, global_vars,
                                parent_agent, task_id, progress_callback,
                                arguments, options.max_rework_attempts,
                            )
                            node_scores[result.node_id] = review_data

                        run.node_results[result.node_id] = result
                        save_workflow_state(run)

                        if result.status == NodeStatus.FAILED:
                            run.status = WorkflowStatus.FAILED
                            run.error = f"Node '{result.node_id}' failed: {result.error}"
                            run.end_time = time.time()
                            save_workflow_state(run)
                            # Cancel remaining futures
                            for f in futures:
                                f.cancel()
                            return run

                        if result.status == NodeStatus.CANCELLED:
                            run.status = WorkflowStatus.CANCELLED
                            run.error = result.output
                            run.end_time = time.time()
                            save_workflow_state(run)
                            return run

                        completed_count += 1

            # === Scheduler check after each layer ===
            if scheduler and layer_idx < len(layers) - 1:
                decision = scheduler.check_layer(
                    layer_idx, len(layers), run.node_results)
                _notify(progress_callback, "scheduler_decision", "",
                        f"Scheduler: {decision.action} — {decision.reason}")

                if decision.action == "abort":
                    run.status = WorkflowStatus.CANCELLED
                    run.error = f"Scheduler abort: {decision.reason}"
                    run.end_time = time.time()
                    save_workflow_state(run)
                    return run

                if decision.action == "retry" and decision.target_node:
                    target_id = decision.target_node
                    if target_id in run.node_results and target_id in node_map:
                        _notify(progress_callback, "scheduler_retry", target_id,
                                f"Re-executing with: {decision.guidance}")
                        # Remove old result and re-execute
                        del run.node_results[target_id]
                        retry_node = node_map[target_id]
                        if decision.guidance:
                            import copy as _copy
                            retry_node = _copy.deepcopy(retry_node)
                            if retry_node.goal:
                                retry_node.context = (
                                    (retry_node.context or "") +
                                    f"\n\n## Scheduler guidance:\n{decision.guidance}"
                                )
                        retry_result = _execute_node_with_retry(
                            retry_node, run.node_results, global_vars,
                            parent_agent, task_id, progress_callback
                        )
                        run.node_results[retry_result.node_id] = retry_result
                        save_workflow_state(run)

        # All nodes completed successfully
        run.status = WorkflowStatus.COMPLETED
        run.end_time = time.time()
        save_workflow_state(run)
        _notify(progress_callback, "workflow_completed", "",
                f"Workflow '{workflow_name}' completed in {run.end_time - run.start_time:.1f}s")

        # Auto-save completed workflow as a reusable template (if not unnamed)
        if not template_name and workflow_name and workflow_name != "unnamed-workflow":
            try:
                from tools.workflow_planner import TemplateStore
                store = TemplateStore()
                existing = store.load(workflow_name)
                if existing is None:
                    store.save(workflow_name, yaml_text, from_run_id=run.run_id)
                    _notify(progress_callback, "template_saved", "",
                            f"Auto-saved as template '{workflow_name}' for future reuse")
                else:
                    # Template already exists — bump usage count with a neutral score
                    store.update_stats(workflow_name, 7.0)
                    _notify(progress_callback, "template_updated", "",
                            f"Template '{workflow_name}' already exists — usage count updated")
            except Exception as e:
                logger.warning("Failed to auto-save template: %s", e)

        # Post-run retrospective: analyse execution chain for optimization opportunities
        if parent_agent and workflow_name and workflow_name != "unnamed-workflow":
            try:
                from tools.workflow_planner import retrospect_workflow
                retro = retrospect_workflow(
                    workflow_name=workflow_name,
                    nodes=nodes,
                    node_results=run.node_results,
                    total_duration=run.end_time - run.start_time,
                    parent_agent=parent_agent,
                )
                if retro:
                    run.retrospect = retro
                    verdict = retro.get("verdict", "")
                    suggestions = retro.get("suggestions", [])
                    high = [s for s in suggestions if s.get("priority") == "high"]
                    summary = f"Verdict: {verdict}"
                    if high:
                        summary += f" | {len(high)} high-priority suggestion(s)"
                    _notify(progress_callback, "retrospect", "",
                            f"Execution review — {summary}")
            except Exception as e:
                logger.warning("Failed to run retrospective: %s", e)

        # Save template execution stats if from a template
        if template_name and node_scores:
            try:
                from tools.workflow_planner import TemplateStore
                store = TemplateStore()
                all_scores = [s.get("completeness", 0) + s.get("quality", 0)
                              for s in node_scores.values()]
                overall = sum(all_scores) / len(all_scores) / 2 if all_scores else 7.0
                store.update_stats(template_name, overall)
                store.save_execution_record(template_name, run.run_id,
                                             node_scores, overall)
                _notify(progress_callback, "template_stats", "",
                        f"Updated template '{template_name}' stats (score: {overall:.1f})")
            except Exception as e:
                logger.warning("Failed to save template stats: %s", e)

    except Exception as e:
        run.status = WorkflowStatus.FAILED
        run.error = f"Workflow execution error: {e}"
        run.end_time = time.time()
        save_workflow_state(run)
        logger.exception("Workflow execution failed: %s", e)

    return run


def _review_and_maybe_rework(
    reviewer, node: 'NodeDef', result: 'NodeResult',
    node_outputs: Dict[str, 'NodeResult'],
    global_vars: Dict[str, str],
    parent_agent, task_id, progress_callback,
    overall_task: str, max_rework: int,
) -> Tuple['NodeResult', dict]:
    """Run reviewer on a node result, rework if below threshold.

    Returns (final_result, review_data_dict).
    """
    node_task = node.goal or node.prompt_text or ""
    review = reviewer.review(
        node.id, node_task, result.output, overall_task)

    review_data = {
        "completeness": review.completeness,
        "quality": review.quality,
        "verdict": review.verdict,
        "issues": review.issues,
    }

    _notify(progress_callback, "review", node.id,
            f"Review: {review.verdict} "
            f"(C={review.completeness}/10, Q={review.quality}/10)"
            + (f" Issues: {'; '.join(review.issues[:3])}"
               if review.issues else ""))

    if review.verdict == "pass":
        return result, review_data

    # Rework: re-execute with reviewer feedback
    for attempt in range(max_rework):
        _notify(progress_callback, "rework", node.id,
                f"Rework {attempt + 1}/{max_rework}: {review.guidance[:200]}")

        try:
            from tools.workflow_planner import add_reviewer_feedback
            enhanced_node = add_reviewer_feedback(node, review.guidance)
        except ImportError:
            break

        rework_result = _execute_node_with_retry(
            enhanced_node, node_outputs, global_vars,
            parent_agent, task_id, progress_callback
        )

        if rework_result.status != NodeStatus.COMPLETED:
            _notify(progress_callback, "rework_failed", node.id,
                    f"Rework failed: {rework_result.error}")
            return rework_result, review_data

        # Re-review
        review = reviewer.review(
            node.id, node_task, rework_result.output, overall_task)
        review_data = {
            "completeness": review.completeness,
            "quality": review.quality,
            "verdict": review.verdict,
            "issues": review.issues,
        }
        _notify(progress_callback, "review", node.id,
                f"Re-review: {review.verdict} "
                f"(C={review.completeness}/10, Q={review.quality}/10)")

        if review.verdict == "pass":
            return rework_result, review_data
        result = rework_result

    # Exhausted rework attempts — accept current result
    _notify(progress_callback, "rework_accepted", node.id,
            f"Max rework reached, accepting current output")
    return result, review_data


def _execute_node_with_retry(
    node: NodeDef,
    node_outputs: Dict[str, NodeResult],
    global_vars: Dict[str, str],
    parent_agent=None,
    task_id: Optional[str] = None,
    progress_callback=None,
) -> NodeResult:
    """Execute a node, handling condition checks and retries."""

    # Check 'when' condition
    if node.when:
        if not evaluate_condition(node.when, node_outputs):
            _notify(progress_callback, "node_skipped", node.id,
                    f"Condition not met: {node.when}")
            return NodeResult(
                node_id=node.id, status=NodeStatus.SKIPPED,
                output=f"Skipped: condition '{node.when}' evaluated to false",
            )

    retry_cfg = node.retry or RetryConfig(max_attempts=1)
    last_result = None

    for attempt in range(1, retry_cfg.max_attempts + 1):
        _notify(progress_callback, "node_started", node.id,
                f"Executing ({attempt}/{retry_cfg.max_attempts})" if retry_cfg.max_attempts > 1
                else "Executing")

        # Dispatch to type-specific executor
        if node.node_type == NodeType.AGENT:
            result = _execute_agent_node(node, node_outputs, global_vars, parent_agent)
        elif node.node_type == NodeType.PROMPT:
            result = _execute_prompt_node(node, node_outputs, global_vars, parent_agent)
        elif node.node_type == NodeType.BASH:
            result = _execute_bash_node(node, node_outputs, global_vars, task_id)
        elif node.node_type == NodeType.CLAUDE_CODE:
            result = _execute_claude_code_node(node, node_outputs, global_vars, task_id)
        elif node.node_type == NodeType.LOOP:
            result = _execute_loop_node(node, node_outputs, global_vars, parent_agent)
        elif node.node_type == NodeType.GATE:
            result = _execute_gate_node(node)
        elif node.node_type == NodeType.CANCEL:
            result = _execute_cancel_node(node, node_outputs, global_vars)
        else:
            result = NodeResult(
                node_id=node.id, status=NodeStatus.FAILED,
                error=f"Unknown node type: {node.node_type}",
            )

        result.attempts = attempt
        last_result = result

        if result.status in (NodeStatus.COMPLETED, NodeStatus.SKIPPED,
                             NodeStatus.CANCELLED, NodeStatus.WAITING_APPROVAL):
            _notify(progress_callback, "node_completed", node.id,
                    f"✅ {result.status.value} ({result.duration_seconds:.1f}s)")
            return result

        # Failed — check retry
        if _should_retry(result.error or "", retry_cfg, attempt):
            delay = retry_cfg.delay_seconds * (retry_cfg.backoff_multiplier ** (attempt - 1))
            _notify(progress_callback, "node_retry", node.id,
                    f"Retry in {delay:.0f}s (attempt {attempt}/{retry_cfg.max_attempts}): {result.error}")
            time.sleep(delay)
        else:
            break

    _notify(progress_callback, "node_failed", node.id,
            f"❌ Failed after {last_result.attempts} attempt(s): {last_result.error}")
    return last_result


def _notify(callback, event_type: str, node_id: str, message: str):
    """Fire a progress notification if callback is available."""
    if callback:
        try:
            callback(event_type, node_id, message)
        except Exception:
            pass
    logger.info("workflow [%s] %s: %s", event_type, node_id, message)


# =============================================================================
# Tool Interface — called by the agent
# =============================================================================

def workflow_run_tool(
    workflow: Optional[str] = None,
    arguments: Optional[str] = None,
    action: str = "run",
    run_id: Optional[str] = None,
    template_name: Optional[str] = None,
    parent_agent=None,
    task_id: Optional[str] = None,
) -> str:
    """Main tool entry point for workflow execution.

    Actions:
      - run: Execute a workflow (workflow=YAML string, arguments=user input)
      - auto: Auto-plan a workflow from natural language, show YAML for approval
      - resume: Resume a failed/paused workflow (run_id required)
      - status: Check status of a workflow run (run_id required)
      - list: List recent workflow runs
      - save_template: Save a workflow as a reusable template
      - list_templates: List available templates
      - optimize: Analyze and suggest template improvements
    """
    if action == "list":
        runs = list_workflow_runs()
        return json.dumps({"runs": runs}, ensure_ascii=False)

    if action == "list_templates":
        try:
            from tools.workflow_planner import TemplateStore
            store = TemplateStore()
            templates = store.list()
            return json.dumps({"templates": templates}, ensure_ascii=False)
        except ImportError:
            return json.dumps({"error": "workflow_planner module not available"})

    if action == "status":
        if not run_id:
            return json.dumps({"error": "run_id is required for status action"})
        state = load_workflow_state(run_id)
        if state:
            return json.dumps(state, ensure_ascii=False)
        return json.dumps({"error": f"No workflow run found with id: {run_id}"})

    if action == "auto":
        if not arguments:
            return json.dumps({
                "error": "arguments (task description) is required for auto action"})
        try:
            from tools.workflow_planner import (
                TemplateStore, match_template, auto_plan)
            store = TemplateStore()
            templates = store.list()

            # Step 1: Try to match existing template
            matched = None
            template_data = None
            if templates:
                matched = match_template(arguments, templates, parent_agent)

            if matched:
                tname, confidence, reasoning = matched
                template_data = store.load(tname)
                result = {
                    "action": "auto",
                    "status": "template_matched",
                    "template_name": tname,
                    "confidence": confidence,
                    "reasoning": reasoning,
                }
            else:
                result = {
                    "action": "auto",
                    "status": "new_task_type",
                }

            # Step 2: Generate YAML (from template or from scratch)
            yaml_text = auto_plan(arguments, template_data, parent_agent)
            if not yaml_text:
                return json.dumps({
                    "error": "Failed to generate workflow YAML",
                    **result})

            result["workflow_yaml"] = yaml_text
            result["requires_approval"] = matched is None
            result["message"] = (
                "Template matched — ready to execute."
                if matched
                else "New task type — please review the workflow YAML before executing."
            )
            return json.dumps(result, ensure_ascii=False)

        except ImportError:
            return json.dumps({
                "error": "workflow_planner module not available"})

    if action == "save_template":
        if not workflow:
            return json.dumps({
                "error": "workflow YAML is required for save_template"})
        if not template_name:
            return json.dumps({
                "error": "template_name is required for save_template"})
        try:
            from tools.workflow_planner import (
                TemplateStore, generalize_workflow)
            store = TemplateStore()

            # Generalize the workflow if arguments present
            if arguments:
                generalized = generalize_workflow(
                    workflow, arguments, parent_agent)
                if generalized:
                    workflow = generalized

            result = store.save(
                name=template_name,
                workflow_yaml=workflow,
                description=arguments or "",
                from_run_id=run_id,
            )
            return json.dumps(result, ensure_ascii=False)
        except ImportError:
            return json.dumps({
                "error": "workflow_planner module not available"})

    if action == "optimize":
        if not template_name:
            return json.dumps({
                "error": "template_name is required for optimize"})
        try:
            from tools.workflow_planner import (
                TemplateStore, analyze_template_performance,
                optimize_template)
            store = TemplateStore()

            analysis = analyze_template_performance(template_name, store)
            result = {"analysis": analysis}

            if (analysis and analysis.get("status") == "analyzed"
                    and analysis.get("weak_nodes")):
                improved = optimize_template(
                    template_name, store, parent_agent)
                if improved:
                    result["improved_yaml"] = improved
                    result["message"] = (
                        "Template has weak nodes. Improved YAML generated. "
                        "Review and save with action='save_template'."
                    )
            elif analysis and analysis.get("status") == "analyzed":
                result["message"] = (
                    f"Template performing well "
                    f"(avg score: {analysis.get('avg_overall_score')}). "
                    f"No optimization needed."
                )
            return json.dumps(result, ensure_ascii=False)
        except ImportError:
            return json.dumps({
                "error": "workflow_planner module not available"})

    if action == "resume":
        if not run_id:
            return json.dumps({"error": "run_id is required for resume action"})
        state = load_workflow_state(run_id)
        if not state:
            return json.dumps({"error": f"No workflow run found with id: {run_id}"})
        if not workflow:
            return json.dumps({"error": "workflow YAML is required to resume (node definitions needed)"})

        # Build progress callback
        progress_msgs = []
        def _progress(event_type, node_id, message):
            progress_msgs.append(f"[{event_type}] {node_id}: {message}" if node_id else f"[{event_type}] {message}")

        run = execute_workflow(
            yaml_text=workflow,
            arguments=state.get("arguments", arguments or ""),
            parent_agent=parent_agent,
            task_id=task_id,
            resume_run_id=run_id,
            progress_callback=_progress,
            template_name=template_name,
        )
        return _format_run_result(run, progress_msgs)

    if action == "run":
        if not workflow:
            return json.dumps({"error": "workflow YAML is required for run action"})

        # Validate YAML first
        try:
            parse_workflow(workflow)
        except Exception as e:
            return json.dumps({"error": f"Invalid workflow: {e}"})

        # Build progress callback
        progress_msgs = []
        def _progress(event_type, node_id, message):
            progress_msgs.append(f"[{event_type}] {node_id}: {message}" if node_id else f"[{event_type}] {message}")

        run = execute_workflow(
            yaml_text=workflow,
            arguments=arguments or "",
            parent_agent=parent_agent,
            task_id=task_id,
            progress_callback=_progress,
            template_name=template_name,
        )
        return _format_run_result(run, progress_msgs)

    return json.dumps({"error": f"Unknown action: {action}. Use: run, auto, resume, status, list, save_template, list_templates, optimize"})


def _format_run_result(run: WorkflowRun, progress_msgs: List[str]) -> str:
    """Format a workflow run result for the agent."""
    result = {
        "run_id": run.run_id,
        "workflow_name": run.workflow_name,
        "status": run.status.value,
        "duration_seconds": round(run.end_time - run.start_time, 1) if run.end_time else 0,
        "error": run.error,
        "node_results": {},
        "progress_log": progress_msgs[-30:],  # Last 30 events
    }
    for nid, nr in run.node_results.items():
        result["node_results"][nid] = {
            "status": nr.status.value,
            "output_preview": nr.output[:500] if nr.output else "",
            "error": nr.error,
            "duration_seconds": round(nr.duration_seconds, 1),
            "attempts": nr.attempts,
        }
        if nr.parsed_output:
            result["node_results"][nid]["parsed_output"] = nr.parsed_output
    if run.retrospect:
        result["retrospect"] = run.retrospect
    return json.dumps(result, ensure_ascii=False)


# =============================================================================
# Tool Schema & Registration
# =============================================================================

WORKFLOW_SCHEMA = {
    "name": "workflow",
    "description": (
        "Execute declarative YAML workflows that orchestrate multiple agents, "
        "shell commands, and control flow as a DAG (Directed Acyclic Graph).\n\n"
        "WORKFLOW YAML FORMAT:\n"
        "```yaml\n"
        "name: my-workflow\n"
        "options:                          # Enable scheduler & reviewer\n"
        "  scheduler: true                 # Monitor execution, adjust flow\n"
        "  reviewer: true                  # Adversarial quality checks\n"
        "  reviewer_threshold: 7           # Min score 0-10\n"
        "nodes:\n"
        "  - id: step1\n"
        "    bash: 'echo hello'          # Shell command\n"
        "  - id: step2\n"
        "    goal: 'Analyze the data'    # Agent task (spawns subagent)\n"
        "    context: 'Data: $step1.output'\n"
        "    depends_on: [step1]\n"
        "  - id: step3\n"
        "    prompt: 'Summarize: $step2.output'  # LLM prompt\n"
        "    depends_on: [step2]\n"
        "    when: \"$step2.status == 'completed'\"  # Conditional\n"
        "  - id: step4\n"
        "    claude_code: 'Implement the feature'  # Claude Code CLI coding task\n"
        "    workdir: ~/my-project\n"
        "    allowed_tools: [Read, Edit, Write, Bash]\n"
        "    max_turns: 15\n"
        "    model: sonnet\n"
        "    depends_on: [step3]\n"
        "    context: 'Based on: $step3.output'\n"
        "```\n\n"
        "NODE TYPES:\n"
        "- goal: Complex tasks needing tools (web, code, files) — spawns subagent\n"
        "- prompt: Simple LLM reasoning/summarization (no tools)\n"
        "- bash: Shell command execution\n"
        "- claude_code: Coding task via Claude Code CLI (implementing features, fixing "
        "bugs, writing tests, refactoring). Fields: claude_code (task), workdir, "
        "allowed_tools, max_turns, model, max_budget_usd, context\n"
        "- loop: Repeat until condition or max iterations\n"
        "- gate: Pause for human approval\n"
        "- cancel: Abort workflow with reason\n\n"
        "ACTIONS:\n"
        "- run: Execute a workflow YAML\n"
        "- auto: Auto-plan workflow from task description (new types need approval)\n"
        "- resume: Resume failed/paused workflow (run_id required)\n"
        "- status: Check run status (run_id required)\n"
        "- list: List recent runs\n"
        "- save_template: Save workflow as reusable template\n"
        "- list_templates: List available templates\n"
        "- optimize: Analyze template performance, suggest improvements\n\n"
        "TWO BUILT-IN ROLES:\n"
        "- Scheduler: lightweight monitor between layers (no task execution)\n"
        "- Reviewer: adversarial quality evaluator (triggers rework if below threshold)\n\n"
        "Enable via options.scheduler and options.reviewer in YAML."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["run", "auto", "resume", "status", "list",
                         "save_template", "list_templates", "optimize"],
                "description": (
                    "'run': Execute workflow YAML. "
                    "'auto': Auto-plan from task description (arguments). "
                    "'resume': Resume failed/paused workflow. "
                    "'status': Check run status. "
                    "'list': List recent runs. "
                    "'save_template': Save workflow as template. "
                    "'list_templates': List templates. "
                    "'optimize': Analyze + improve template."
                ),
            },
            "workflow": {
                "type": "string",
                "description": (
                    "YAML workflow definition. Required for 'run', 'resume', 'save_template'."
                ),
            },
            "arguments": {
                "type": "string",
                "description": (
                    "Task description for 'auto', or user arguments as $ARGS for other actions."
                ),
            },
            "run_id": {
                "type": "string",
                "description": "Workflow run ID for 'resume', 'status', 'save_template'.",
            },
            "template_name": {
                "type": "string",
                "description": "Template name for 'save_template', 'optimize'.",
            },
        },
        "required": ["action"],
    },
}


# --- Registration ---
from tools.registry import registry, tool_error

registry.register(
    name="workflow",
    toolset="delegation",  # Same toolset as delegate_task
    schema=WORKFLOW_SCHEMA,
    handler=lambda args, **kw: workflow_run_tool(
        workflow=args.get("workflow"),
        arguments=args.get("arguments"),
        action=args.get("action", "run"),
        run_id=args.get("run_id"),
        template_name=args.get("template_name"),
        parent_agent=kw.get("parent_agent"),
        task_id=kw.get("task_id"),
    ),
    emoji="🔄",
)
