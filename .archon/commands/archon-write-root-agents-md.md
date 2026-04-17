---
description: 汇总各模块 AGENTS.md，写出根 AGENTS.md（纯索引层，≤100行）
argument-hint: <project_root>
---

# archon-write-root-agents-md

**Goal**: 用各模块已生成的 AGENTS.md 和分析计划，写出精炼的根 AGENTS.md

**输入**:
- `$ARTIFACTS_DIR/index-plan.json` — 规划文件
- `$ARTIFACTS_DIR/root-agents-md-skeleton.md` — 骨架
- 各模块已写入的 AGENTS.md

---

## Phase 1: 汇总现状

```bash
cat $ARTIFACTS_DIR/index-plan.json
echo "---"
cat $ARTIFACTS_DIR/root-agents-md-skeleton.md
```

```bash
# 检查哪些模块 AGENTS.md 已成功写入
python3 - << 'EOF'
import json, os
plan = json.load(open(os.environ.get("ARTIFACTS_DIR","") + "/index-plan.json"))
project_dir = plan.get("project_dir", ".")
for m in plan.get("index_tree", []):
    path = os.path.join(project_dir, m["agents_md_path"])
    exists = os.path.exists(path)
    size = os.path.getsize(path) if exists else 0
    print(f"{'✅' if exists else '❌'} {m['agents_md_path']} ({size} bytes)")
EOF
```

```bash
# 读现有根 AGENTS.md（如有，作为改写基础）
cat AGENTS.md 2>/dev/null | head -50 || echo "NO EXISTING ROOT AGENTS.md"
```

**PHASE_1_CHECKPOINT:**
- [ ] 已知道哪些模块 AGENTS.md 已写入

---

## Phase 2: 读各模块 AGENTS.md 首部（获取摘要）

```bash
# 读每个模块 AGENTS.md 前 5 行（获取一句话描述）
for f in tools/AGENTS.md agent/AGENTS.md gateway/AGENTS.md hermes_cli/AGENTS.md; do
  [ -f "$f" ] && echo "=== $f ===" && head -5 "$f"
done
```

同时读取现有 AGENTS.md 中已有的有价值内容（Quick Start、禁止项等），这些要保留。

---

## Phase 3: 写根 AGENTS.md

根 AGENTS.md 的写法原则：
- **模块索引表**：每个模块一行，含文件数、一句话职责、AGENTS.md 链接
- **规模小的模块**（< 15 文件，已在 root_only_modules 中）：直接在根层列文件，不单独建 AGENTS.md
- **业务流程索引**：列出 3-5 条核心端到端流程，每条指向入口文件+函数
- **Quick Start**：保留有价值的命令
- **禁止项**：保留已有的禁止项表（如有）

格式约束：
- 总行数 ≤ 120 行
- 模块索引表：每模块一行
- 业务流程：每条 ≤ 3 行（入口 → 关键节点 → 出口）

写入项目根目录的 AGENTS.md：

```bash
cat > AGENTS.md << 'ROOT_AGENTS'

# [项目名]

[一句话定位]

## Quick Start

```bash
# [关键命令]
```

## 模块索引

**规则：进入任何子目录前，先 `cat <dir>/AGENTS.md`（如有）**

| 目录/文件 | 职责 | 文件数 | 索引 |
|----------|------|--------|------|
| `tools/` | 工具注册、dispatch、执行 | 35 | [AGENTS.md](tools/AGENTS.md) |
| `agent/` | Prompt 组装、context 压缩、模型路由 | 25 | [AGENTS.md](agent/AGENTS.md) |
| `gateway/` | 消息平台网关（Telegram/Discord/Slack…） | 40 | [AGENTS.md](gateway/AGENTS.md) |
| `hermes_cli/` | CLI 交互层、slash command 分发 | 30 | [AGENTS.md](hermes_cli/AGENTS.md) |
| `run_agent.py` | AIAgent 核心类，conversation loop | 1 | 直接读 |
| `model_tools.py` | 工具发现、_discover_tools() | 1 | 直接读 |
| `cron/` | 定时任务调度 | 5 | 见下方 |

### 规模小模块（直接列文件）

**cron/**
| 文件 | 职责 |
|------|------|
| `jobs.py` | Job 定义和持久化 |
| `scheduler.py` | 触发调度 |

## 业务流程索引

### 对话处理链
```
用户消息 → run_agent.py:run_conversation() → model_tools.py:handle_function_call()
  → tools/registry.py:dispatch() → 具体工具 → 返回 JSON → 写入 messages
```

### 消息平台接入链（以 Telegram 为例）
```
Telegram update → gateway/platforms/telegram.py:handle_update()
  → gateway/run.py:process_message() → AIAgent.chat() → 发回 Telegram
```

### 新增工具流程
见 [tools/AGENTS.md](tools/AGENTS.md) §新增工具步骤

ROOT_AGENTS
```

**PHASE_3_CHECKPOINT:**
- [ ] 根 AGENTS.md 已写入
- [ ] 行数 ≤ 120
- [ ] 包含模块索引表（所有模块，含链接）
- [ ] 包含至少 2 条业务流程

---

## Success Criteria

- **DONE**: 根 AGENTS.md 已写入，AI 读完后能明确知道"我要找 X，去读 Y/AGENTS.md"
