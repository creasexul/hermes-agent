#!/usr/bin/env python3
"""
Workflow Planner — Auto-Planning, Templates, Scheduler & Reviewer

Extends the workflow executor with intelligent planning and quality control:
1. TemplateStore — persist reusable workflow templates with semantic matching
2. Auto-planning — generate workflow YAML from natural language descriptions
3. WorkflowScheduler — lightweight LLM monitor between DAG layers (no task participation)
4. WorkflowReviewer — adversarial quality evaluator for node outputs
5. Template optimization — improve templates based on execution history

Architecture Notes:
- Scheduler and Reviewer use FRESH LLM contexts per call (no accumulated state)
- This prevents context overflow from derailing fixed execution flows
- The scheduler only sees summaries (~300 chars per node), never full outputs
"""

import copy
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# =============================================================================
# Lightweight LLM Helpers
# =============================================================================

def _llm_call(parent_agent, system: str, user: str, max_tokens: int = 2000) -> str:
    """Direct LLM call using parent agent's client. Fresh context each call.

    Critical design property: each call is completely independent — no shared
    message history. This ensures scheduler/reviewer don't pollute or get
    polluted by the main agent's context window.
    """
    if parent_agent is None:
        return ""
    try:
        response = parent_agent.client.chat.completions.create(
            model=parent_agent.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        content = response.choices[0].message.content
        return content.strip() if content else ""
    except Exception as e:
        logger.warning("Lightweight LLM call failed: %s", e)
        return ""


def _llm_json(parent_agent, system: str, user: str,
              max_tokens: int = 2000) -> Optional[dict]:
    """LLM call that extracts JSON from the response."""
    raw = _llm_call(parent_agent, system, user, max_tokens)
    if not raw:
        return None
    # Strategy 1: direct parse
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        pass
    # Strategy 2: code block
    m = re.search(r'```(?:json)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass
    # Strategy 3: first { ... }
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            pass
    return None


# =============================================================================
# Template Store
# =============================================================================

def _get_templates_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = Path(get_hermes_home())
    except ImportError:
        base = Path.home() / ".hermes"
    d = base / "workflows" / "templates"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_history_dir() -> Path:
    try:
        from hermes_constants import get_hermes_home
        base = Path(get_hermes_home())
    except ImportError:
        base = Path.home() / ".hermes"
    d = base / "workflows" / "history"
    d.mkdir(parents=True, exist_ok=True)
    return d


class TemplateStore:
    """Manages workflow templates on disk.

    Templates are YAML files in ~/.hermes/workflows/templates/.
    Each includes metadata (triggers, usage stats, version) alongside node defs.
    """

    def __init__(self):
        self.dir = _get_templates_dir()

    def list(self) -> List[dict]:
        """List all templates with metadata (lightweight, no full YAML)."""
        try:
            import yaml
        except ImportError:
            return []
        templates = []
        for f in sorted(self.dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(f.read_text())
                templates.append({
                    "name": data.get("name", f.stem),
                    "description": data.get("description", ""),
                    "triggers": data.get("triggers", []),
                    "usage_count": data.get("usage_count", 0),
                    "avg_score": data.get("avg_score"),
                    "version": data.get("version", 1),
                    "created_at": data.get("created_at", ""),
                    "node_count": len(data.get("nodes", [])),
                })
            except Exception:
                continue
        return templates

    def load(self, name: str) -> Optional[dict]:
        """Load a template as parsed dict."""
        f = self.dir / f"{name}.yaml"
        if not f.exists():
            return None
        try:
            import yaml
            return yaml.safe_load(f.read_text())
        except Exception:
            return None

    def load_yaml(self, name: str) -> Optional[str]:
        """Load raw YAML text for a template."""
        f = self.dir / f"{name}.yaml"
        if not f.exists():
            return None
        return f.read_text()

    def save(self, name: str, workflow_yaml: str, description: str = "",
             triggers: List[str] = None, from_run_id: str = None) -> dict:
        """Save a workflow as a reusable template."""
        try:
            import yaml
        except ImportError:
            return {"error": "PyYAML required"}

        try:
            data = yaml.safe_load(workflow_yaml)
            if not isinstance(data, dict):
                data = {"nodes": []}
        except Exception:
            data = {"nodes": []}

        data["name"] = name
        if description:
            data["description"] = description
        if triggers:
            data["triggers"] = triggers
        data.setdefault("version", 1)
        data.setdefault("usage_count", 0)
        data.setdefault("avg_score", None)
        data["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if from_run_id:
            data["created_from_run"] = from_run_id

        f = self.dir / f"{name}.yaml"
        f.write_text(yaml.dump(data, allow_unicode=True,
                               default_flow_style=False, sort_keys=False))
        return {"saved": name, "path": str(f),
                "node_count": len(data.get("nodes", []))}

    def delete(self, name: str) -> bool:
        f = self.dir / f"{name}.yaml"
        if f.exists():
            f.unlink()
            return True
        return False

    def update_stats(self, name: str, score: float):
        """Update usage count and rolling average score after execution."""
        try:
            import yaml
        except ImportError:
            return
        f = self.dir / f"{name}.yaml"
        if not f.exists():
            return
        data = yaml.safe_load(f.read_text())
        count = data.get("usage_count", 0) + 1
        old_avg = data.get("avg_score") or score
        new_avg = (old_avg * (count - 1) + score) / count
        data["usage_count"] = count
        data["avg_score"] = round(new_avg, 2)
        f.write_text(yaml.dump(data, allow_unicode=True,
                               default_flow_style=False, sort_keys=False))

    def save_execution_record(self, template_name: str, run_id: str,
                               node_scores: Dict[str, dict],
                               overall_score: float):
        """Append execution record for optimization analysis."""
        record = {
            "template_name": template_name,
            "run_id": run_id,
            "timestamp": time.time(),
            "node_scores": node_scores,
            "overall_score": overall_score,
        }
        history_file = _get_history_dir() / f"{template_name}.jsonl"
        with open(history_file, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_execution_history(self, template_name: str) -> List[dict]:
        history_file = _get_history_dir() / f"{template_name}.jsonl"
        if not history_file.exists():
            return []
        records = []
        for line in history_file.read_text().strip().split("\n"):
            if line.strip():
                try:
                    records.append(json.loads(line))
                except Exception:
                    continue
        return records


# =============================================================================
# Semantic Template Matching
# =============================================================================

def match_template(task_description: str, templates: List[dict],
                   parent_agent) -> Optional[Tuple[str, float, str]]:
    """Semantically match a task to available templates via LLM.

    Returns (template_name, confidence, reasoning) or None if no good match.
    Confidence threshold: 0.7 (70%).
    """
    if not templates:
        return None

    template_list = "\n".join(
        f"- **{t['name']}**: {t['description']} "
        f"(triggers: {', '.join(t.get('triggers', []))}, "
        f"used {t.get('usage_count', 0)} times, "
        f"score: {t.get('avg_score', 'N/A')})"
        for t in templates
    )

    result = _llm_json(parent_agent,
        system=(
            "You are a workflow template matcher. Given a task description and "
            "available templates, determine if any template is a good semantic match.\n\n"
            "Consider:\n"
            "- Does the task TYPE match the template's purpose?\n"
            "- Are the task's requirements covered by the template's structure?\n"
            "- Would the template's nodes be appropriate for this task?\n\n"
            "Respond with JSON:\n"
            '{"match": "template_name" or null, "confidence": 0.0-1.0, '
            '"reasoning": "explanation"}'
        ),
        user=f"Task: {task_description}\n\nTemplates:\n{template_list}",
        max_tokens=500,
    )

    if result and result.get("match") and (result.get("confidence", 0) >= 0.7):
        return (result["match"], result["confidence"],
                result.get("reasoning", ""))
    return None


# =============================================================================
# Auto-Planning (YAML Generation)
# =============================================================================

def auto_plan(task_description: str, template: Optional[dict],
              parent_agent) -> Optional[str]:
    """Generate workflow YAML from a task description.

    If a template is provided, adapts it. Otherwise creates from scratch.
    Generated workflows include scheduler and reviewer options by default.
    """
    try:
        import yaml
    except ImportError:
        return None

    template_context = ""
    if template:
        clean = {k: v for k, v in template.items()
                 if k in ("name", "nodes", "description")}
        template_context = (
            f"\n\nBase template to adapt:\n```yaml\n"
            f"{yaml.dump(clean, allow_unicode=True, default_flow_style=False)}"
            f"\n```\n"
            f"Adapt this template for the specific task. Keep the node flow but "
            f"adjust goals/prompts."
        )

    raw = _llm_call(parent_agent,
        system=(
            "You are a workflow planner. Generate a YAML workflow that decomposes "
            "a task into a DAG of executable nodes.\n\n"
            "Rules:\n"
            "1. Each node is a focused, independent unit of work\n"
            "2. Identify real dependencies — don't add unnecessary ones\n"
            "3. Nodes that CAN run in parallel SHOULD (no fake dependencies)\n"
            "4. Node types:\n"
            "   - goal: complex tasks needing tools (web, code, files)\n"
            "   - prompt: simple LLM reasoning/summarization (no tools)\n"
            "   - bash: shell commands\n"
            "   - claude_code: coding tasks via Claude Code CLI (implementing features,\n"
            "     fixing bugs, writing tests, refactoring). Fields: claude_code (task),\n"
            "     workdir, allowed_tools, max_turns, model, context. Example:\n"
            "       - id: implement\n"
            "         claude_code: \"Implement the feature described above\"\n"
            "         workdir: /path/to/project\n"
            "         allowed_tools: [Read, Edit, Write, Bash]\n"
            "         max_turns: 15\n"
            "5. Use $ARGS for user-specific task details\n"
            "6. Specify toolsets for agent nodes: [web], [terminal, file], etc.\n"
            "7. Add output_format (JSON schema) for the final node\n"
            "8. Set timeouts: 300s research, 180s analysis, 120s synthesis\n"
            "9. Add options: {scheduler: true, reviewer: true}\n"
            "10. Use $node_id.output for inter-node references\n\n"
            "Output ONLY valid YAML. No fences, no explanation."
        ),
        user=f"Task: {task_description}{template_context}",
        max_tokens=4000,
    )

    if not raw:
        return None

    # Clean code block markers
    m = re.search(r'```(?:yaml)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if m:
        raw = m.group(1)

    # Validate and ensure options
    try:
        parsed = yaml.safe_load(raw)
        if not isinstance(parsed, dict) or "nodes" not in parsed:
            logger.warning("Auto-plan: invalid YAML structure")
            return None
        if "options" not in parsed:
            parsed["options"] = {}
        parsed["options"].setdefault("scheduler", True)
        parsed["options"].setdefault("reviewer", True)
        raw = yaml.dump(parsed, allow_unicode=True,
                        default_flow_style=False, sort_keys=False)
    except Exception as e:
        logger.warning("Auto-plan YAML validation failed: %s", e)
        return None

    return raw.strip()


# =============================================================================
# Scheduler — Lightweight DAG Execution Monitor
# =============================================================================

@dataclass
class SchedulerDecision:
    """Scheduling decision made after observing layer results."""
    action: str  # "continue", "retry", "abort"
    target_node: Optional[str] = None
    guidance: Optional[str] = None
    reason: str = ""


class WorkflowScheduler:
    """Lightweight monitor that observes execution progress and makes
    scheduling decisions.

    KEY PROPERTIES:
    - Does NOT participate in task execution
    - Only sees SUMMARIES of node outputs (~300 chars each)
    - Each check is a fresh LLM call with minimal context
    - Solves context overflow: keeps fixed plan in focus without being
      overwhelmed by intermediate results
    """

    def __init__(self, task_description: str, workflow_yaml: str,
                 parent_agent):
        self.task = task_description
        self.yaml_summary = self._summarize_yaml(workflow_yaml)
        self.parent_agent = parent_agent

    @staticmethod
    def _summarize_yaml(yaml_text: str) -> str:
        """Extract compact plan summary from workflow YAML."""
        try:
            import yaml
            data = yaml.safe_load(yaml_text)
            nodes = data.get("nodes", [])
            lines = [f"Workflow: {data.get('name', 'unnamed')} "
                     f"({len(nodes)} nodes)"]
            for n in nodes:
                nid = n.get("id", "?")
                if "goal" in n:
                    ntype = "agent"
                elif "prompt" in n:
                    ntype = "prompt"
                elif "bash" in n:
                    ntype = "bash"
                else:
                    ntype = "other"
                deps = n.get("depends_on", [])
                desc = (n.get("goal") or n.get("prompt")
                        or n.get("bash") or "")[:100]
                lines.append(
                    f"  [{nid}] ({ntype}) deps={deps}: {desc}")
            return "\n".join(lines)
        except Exception:
            return yaml_text[:500]

    def check_layer(self, layer_idx: int, total_layers: int,
                    node_results: dict) -> SchedulerDecision:
        """Called after each DAG layer completes. Returns scheduling decision.

        Only receives summaries — never full outputs.
        """
        progress_lines = []
        for nid, nr in node_results.items():
            status = (nr.status.value if hasattr(nr.status, "value")
                      else str(nr.status))
            preview = (nr.output or "")[:300].replace("\n", " ").strip()
            err = f" [ERR: {nr.error}]" if nr.error else ""
            progress_lines.append(
                f"  [{nid}] {status}{err}: {preview}")

        progress_text = ("\n".join(progress_lines)
                         if progress_lines else "(no results yet)")

        result = _llm_json(self.parent_agent,
            system=(
                "You are a workflow scheduler monitoring execution.\n"
                "You ONLY observe and decide — NEVER execute tasks.\n"
                "You see brief summaries of completed nodes.\n\n"
                "Decide:\n"
                "- continue: proceed to next layer\n"
                "- retry: re-run a node with guidance (only if output is clearly wrong)\n"
                "- abort: stop workflow (only for fundamental issues)\n\n"
                "Default to 'continue' unless there's a clear problem.\n"
                "Respond with JSON:\n"
                '{"action": "continue"|"retry"|"abort", '
                '"target_node": "node_id", '
                '"guidance": "instructions", '
                '"reason": "explanation"}'
            ),
            user=(
                f"## Task\n{self.task}\n\n"
                f"## Plan\n{self.yaml_summary}\n\n"
                f"## Progress (layer {layer_idx + 1}/{total_layers} done)\n"
                f"{progress_text}\n\n"
                f"Should the workflow continue?"
            ),
            max_tokens=500,
        )

        if not result:
            return SchedulerDecision(
                action="continue",
                reason="Scheduler unavailable — defaulting to continue")

        return SchedulerDecision(
            action=result.get("action", "continue"),
            target_node=result.get("target_node"),
            guidance=result.get("guidance"),
            reason=result.get("reason", ""),
        )


# =============================================================================
# Reviewer
# =============================================================================

from dataclasses import dataclass as _dc
from typing import List as _List


@_dc
class ReviewResult:
    """Result from the reviewer for a single node."""
    node_id: str
    completeness: int  # 0-10
    quality: int       # 0-10
    verdict: str       # "pass" | "fail"
    issues: _List[str]
    guidance: str


class WorkflowReviewer:
    """Adversarial quality reviewer for workflow nodes."""

    def __init__(self, parent_agent, threshold: int = 7):
        self.parent_agent = parent_agent
        self.threshold = threshold

    def review(self, node_id: str, task: str, output: str,
               overall_task: str) -> ReviewResult:
        """Review a node's output. Returns ReviewResult."""
        data = _llm_json(
            self.parent_agent,
            system=(
                "You are an adversarial quality reviewer for AI agent tasks. "
                "Score the output on completeness and quality (0-10 each). "
                "Be strict — only pass outputs that genuinely fulfil the task. "
                "Return JSON only.\n\n"
                "Schema: {\"completeness\": int, \"quality\": int, "
                "\"issues\": [str], \"guidance\": str}"
            ),
            user=(
                f"Overall workflow goal: {overall_task}\n\n"
                f"Node task: {task}\n\n"
                f"Output (first 3000 chars):\n{output[:3000]}"
            ),
            max_tokens=800,
        )
        if not data:
            return ReviewResult(node_id=node_id, completeness=7, quality=7,
                                verdict="pass", issues=[], guidance="")

        completeness = min(10, max(0, int(data.get("completeness", 7))))
        quality = min(10, max(0, int(data.get("quality", 7))))
        verdict = "pass" if (completeness >= self.threshold
                             and quality >= self.threshold) else "fail"
        return ReviewResult(
            node_id=node_id,
            completeness=completeness,
            quality=quality,
            verdict=verdict,
            issues=data.get("issues", []),
            guidance=data.get("guidance", ""),
        )


def add_reviewer_feedback(node, guidance: str):
    """Return a copy of node with reviewer feedback injected into context."""
    import copy
    enhanced = copy.deepcopy(node)
    feedback = f"\n\n## Reviewer feedback (please address):\n{guidance}"
    if enhanced.goal is not None:
        enhanced.context = (enhanced.context or "") + feedback
    elif enhanced.prompt_text is not None:
        enhanced.prompt_text = enhanced.prompt_text + feedback
    return enhanced


# =============================================================================
# Template generalization
# =============================================================================

def generalize_workflow(workflow_yaml: str, arguments: str,
                        parent_agent) -> Optional[str]:
    """Replace run-specific details in a workflow with $ARGS placeholders."""
    if not arguments or parent_agent is None:
        return None
    result = _llm_call(
        parent_agent,
        system=(
            "You are a workflow template specialist. Given a workflow YAML and "
            "the user arguments that were used to create it, replace task-specific "
            "details (names, URLs, dates, IDs) with '$ARGS' so the workflow can "
            "be reused for similar tasks. Keep node structure intact. "
            "Output ONLY valid YAML."
        ),
        user=(
            f"Arguments: {arguments}\n\n"
            f"Workflow YAML:\n```yaml\n{workflow_yaml}\n```"
        ),
        max_tokens=4000,
    )
    if not result:
        return None
    m = re.search(r'```(?:yaml)?\s*\n(.*?)\n```', result, re.DOTALL)
    if m:
        return m.group(1).strip()
    return result.strip()


# =============================================================================
# Template performance analysis + optimization
# =============================================================================

def analyze_template_performance(template_name: str,
                                 store: "TemplateStore") -> dict:
    """Analyse execution history for a template to find weak nodes."""
    history = store.get_execution_history(template_name)
    if not history:
        return {"status": "no_history", "message": "No execution records found"}
    if len(history) < 2:
        return {"status": "insufficient_history",
                "message": "Need at least 2 runs for analysis"}

    node_stats: dict = {}
    overall_scores = []

    for record in history:
        overall_scores.append(record.get("overall_score", 0))
        for nid, scores in record.get("node_scores", {}).items():
            if nid not in node_stats:
                node_stats[nid] = {"completeness": [], "quality": []}
            node_stats[nid]["completeness"].append(
                scores.get("completeness", 7))
            node_stats[nid]["quality"].append(scores.get("quality", 7))

    weak_nodes = []
    for nid, stats in node_stats.items():
        avg_c = sum(stats["completeness"]) / len(stats["completeness"])
        avg_q = sum(stats["quality"]) / len(stats["quality"])
        if avg_c < 7 or avg_q < 7:
            weak_nodes.append({
                "node_id": nid,
                "avg_completeness": round(avg_c, 1),
                "avg_quality": round(avg_q, 1),
            })

    avg_overall = sum(overall_scores) / len(overall_scores)

    trend = "stable"
    if len(overall_scores) >= 3:
        recent = sum(overall_scores[-3:]) / 3
        older = sum(overall_scores[:-3]) / max(len(overall_scores) - 3, 1)
        if recent > older + 0.5:
            trend = "improving"
        elif recent < older - 0.5:
            trend = "declining"

    return {
        "status": "analyzed",
        "run_count": len(history),
        "avg_overall_score": round(avg_overall, 2),
        "trend": trend,
        "weak_nodes": weak_nodes,
        "node_stats": {
            nid: {
                "avg_completeness": round(
                    sum(s["completeness"]) / len(s["completeness"]), 1),
                "avg_quality": round(
                    sum(s["quality"]) / len(s["quality"]), 1),
            }
            for nid, s in node_stats.items()
        },
    }


def optimize_template(template_name: str, store: "TemplateStore",
                      parent_agent) -> Optional[str]:
    """Generate an improved YAML for a template based on performance data."""
    template_data = store.load(template_name)
    if not template_data:
        return None
    analysis = analyze_template_performance(template_name, store)

    try:
        import yaml as _yaml
        template_yaml = _yaml.dump(template_data, allow_unicode=True,
                                   default_flow_style=False)
    except ImportError:
        return None

    raw = _llm_call(
        parent_agent,
        system=(
            "You are a workflow optimization expert. Improve this template "
            "based on performance data.\n\n"
            "Focus on:\n"
            "1. Weak nodes (low scores) — better prompts/goals\n"
            "2. High rework rates — more detail or split into sub-tasks\n"
            "3. Missing dependencies causing gaps\n"
            "4. Sequential nodes that could be parallel\n"
            "5. Increment the 'version' field\n\n"
            "Output ONLY improved YAML."
        ),
        user=(
            f"Template:\n```yaml\n{template_yaml}```\n\n"
            f"Performance:\n"
            f"{json.dumps(analysis, indent=2, ensure_ascii=False)}"
        ),
        max_tokens=4000,
    )

    if not raw:
        return None
    m = re.search(r'```(?:yaml)?\s*\n(.*?)\n```', raw, re.DOTALL)
    if m:
        raw = m.group(1)
    return raw.strip()


# =============================================================================
# Post-Run Retrospective
# =============================================================================

def retrospect_workflow(
    workflow_name: str,
    nodes: list,
    node_results: dict,
    total_duration: float,
    parent_agent,
):
    """Analyse a completed workflow run and suggest optimizations.

    Focuses on two axes:
      - Practicality (实用性保障): reliability, error handling, parallelism, timeouts
      - Quality (质量保障): output completeness, reviewer coverage, rework patterns

    Returns a dict with keys:
      - verdict: "optimal" | "minor_improvements" | "significant_improvements"
      - practicality_issues: list[str]
      - quality_issues: list[str]
      - suggestions: list[{priority, area, suggestion}]
    """
    if parent_agent is None:
        return None

    node_summaries = []
    for nid, nr in node_results.items():
        node_summaries.append({
            "id": nid,
            "status": nr.status.value,
            "duration_s": round(nr.duration_seconds, 1),
            "attempts": nr.attempts,
            "error": nr.error,
            "output_len": len(nr.output) if nr.output else 0,
        })

    dep_map = {n.id: set(n.depends_on) for n in nodes}
    _depth_cache: dict = {}

    def _depth(nid):
        if nid in _depth_cache:
            return _depth_cache[nid]
        deps = dep_map.get(nid, set())
        d = (max(_depth(d) for d in deps) + 1) if deps else 0
        _depth_cache[nid] = d
        return d

    depth_map = {}
    for n in nodes:
        try:
            depth_map[n.id] = _depth(n.id)
        except Exception:
            depth_map[n.id] = 0

    layers: dict = {}
    for n in nodes:
        d = depth_map.get(n.id, 0)
        layers.setdefault(d, []).append(n.id)

    parallelism_info = {
        "total_nodes": len(nodes),
        "layer_count": len(layers),
        "layers": {str(d): ids for d, ids in sorted(layers.items())},
        "sequential_only": all(len(ids) == 1 for ids in layers.values()),
    }

    node_types = {}
    for n in nodes:
        node_types[n.id] = n.node_type.value if hasattr(n.node_type, "value") else str(n.node_type)

    result = _llm_json(
        parent_agent,
        system=(
            "You are a workflow efficiency analyst. Analyse a completed agent workflow "
            "and identify optimization opportunities on two axes:\n\n"
            "1. PRACTICALITY (实用性保障): reliability, error handling, parallelism "
            "opportunities, timeout configuration, retry coverage, node granularity.\n"
            "2. QUALITY (质量保障): output completeness, reviewer/scheduler coverage, "
            "rework patterns, slow nodes that indicate quality problems, "
            "missing validation steps.\n\n"
            "Be concrete and actionable. Return JSON only.\n\n"
            "JSON schema:\n"
            "{\n"
            "  \"verdict\": \"optimal\" | \"minor_improvements\" | \"significant_improvements\",\n"
            "  \"practicality_issues\": [\"...\"],\n"
            "  \"quality_issues\": [\"...\"],\n"
            "  \"suggestions\": [\n"
            "    {\"priority\": \"high\"|\"medium\"|\"low\", \"area\": \"practicality\"|\"quality\","
            " \"suggestion\": \"...\"}\n"
            "  ]\n"
            "}"
        ),
        user=(
            f"Workflow: {workflow_name}\n"
            f"Total duration: {round(total_duration, 1)}s\n\n"
            f"Node types: {json.dumps(node_types, ensure_ascii=False)}\n\n"
            f"Parallelism: {json.dumps(parallelism_info, ensure_ascii=False)}\n\n"
            f"Node results:\n{json.dumps(node_summaries, indent=2, ensure_ascii=False)}"
        ),
        max_tokens=1200,
    )
    return result
