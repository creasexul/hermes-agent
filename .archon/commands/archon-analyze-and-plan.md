---
description: 深度分析项目，规划索引层级和拆分策略
argument-hint: <project_root>
---

# archon-analyze-and-plan

**Goal**: 读懂项目，按"索引效率 + 内聚性"原则规划整个 AGENTS.md 层级树

**输入**: `$scan-skeleton.output` — 项目骨架扫描结果

---

## Phase 1: 读取扫描数据 + 读入口文件

```bash
cat $ARTIFACTS_DIR/scan-skeleton.txt
```

读取上游扫描结果后，主动读取关键文件来理解项目：

```bash
# 读现有根 AGENTS.md（如有）
cat AGENTS.md 2>/dev/null || echo "NO ROOT AGENTS.md"

# 读 README
head -60 README.md 2>/dev/null || echo "NO README"

# 读项目入口（Python/TS/Go 等）
# 根据扫描结果中的语言分布，找入口文件
find . -maxdepth 2 \( -name "main.py" -o -name "run_agent.py" -o -name "cli.py" \
  -o -name "main.ts" -o -name "index.ts" -o -name "main.go" \
  -o -name "main.rs" -o -name "App.kt" \) \
  -not -path "*/test*" -not -path "*/__pycache__/*" | head -5
```

对找到的每个入口文件，读前 60 行。

**PHASE_1_CHECKPOINT:**
- [ ] 已获取项目语言/框架
- [ ] 已读至少 1 个入口文件

---

## Phase 2: 逐目录扫描，识别大型高内聚模块

对顶层每个目录（源码文件数 > 20 的），执行：

```bash
# 统计子目录文件分布
find <dir> -maxdepth 2 -type d | while read d; do
  count=$(find "$d" -maxdepth 1 \( -name "*.py" -o -name "*.ts" -o -name "*.kt" -o -name "*.java" \) | wc -l)
  [ "$count" -gt 3 ] && echo "$count  $d"
done | sort -rn | head -20
```

对文件数 > 30 的子目录，进一步读其核心文件（__init__.py、index.ts、registry.py 等）理解职责。

**判断标准**（边探索边应用）：

| 条件 | 决策 |
|------|------|
| 目录文件数 < 20，职责单一 | → 叶子节点，在父层 AGENTS.md 直接列文件表 |
| 目录文件数 20-80，职责清晰 | → 独立 AGENTS.md，不再往下拆 |
| 目录文件数 > 80，或含 3+ 高内聚子域 | → 独立 AGENTS.md + 继续拆子层 |
| 横切关注点（网络/DI/路由等） | → 独立 AGENTS.md，附业务调用栈 |

**PHASE_2_CHECKPOINT:**
- [ ] 已扫描所有顶层目录
- [ ] 已识别出"大型高内聚模块"列表

---

## Phase 3: 规划层级树，输出计划文件

综合分析，输出以下两个文件：

### 3.1 层级规划（给其他节点用）

写入 `$ARTIFACTS_DIR/index-plan.json`：

```json
{
  "project_name": "hermes-agent",
  "project_dir": "/path/to/project",
  "language": "Python",
  "framework": "custom",
  "root_agents_md_exists": true,
  "index_tree": [
    {
      "path": "tools/",
      "agents_md_path": "tools/AGENTS.md",
      "file_count": 35,
      "priority": "critical",
      "cohesion": "high",
      "needs_subindex": false,
      "description": "工具注册与执行系统",
      "extra_dimensions": ["工具调用链（registry → handler → 返回 JSON）"],
      "slot": 1
    },
    {
      "path": "agent/",
      "agents_md_path": "agent/AGENTS.md",
      "file_count": 25,
      "priority": "critical",
      "cohesion": "high",
      "needs_subindex": false,
      "description": "Agent 内部核心（prompt、压缩、路由）",
      "extra_dimensions": ["Agent loop 调用链", "system prompt 组装路径"],
      "slot": 2
    },
    {
      "path": "gateway/",
      "agents_md_path": "gateway/AGENTS.md",
      "file_count": 40,
      "priority": "high",
      "cohesion": "high",
      "needs_subindex": true,
      "subindex": [
        {
          "path": "gateway/platforms/",
          "agents_md_path": "gateway/platforms/AGENTS.md",
          "description": "各平台 adapter（telegram/discord/slack…）"
        }
      ],
      "description": "消息平台网关",
      "extra_dimensions": ["消息接收→Agent处理→发送 完整链路"],
      "slot": 3
    },
    {
      "path": "hermes_cli/",
      "agents_md_path": "hermes_cli/AGENTS.md",
      "file_count": 30,
      "priority": "high",
      "cohesion": "medium",
      "needs_subindex": false,
      "description": "CLI 交互层",
      "extra_dimensions": ["slash command 分发链"],
      "slot": 4
    }
  ],
  "root_only_modules": [
    {
      "path": "cron/",
      "description": "定时任务调度，文件少，在根 AGENTS.md 直接描述"
    }
  ]
}
```

### 3.2 根 AGENTS.md 草稿骨架

写入 `$ARTIFACTS_DIR/root-agents-md-skeleton.md`，只含结构框架（不含实质内容），供后续节点填充：

```markdown
# [项目名]

[一句话：项目是什么]

## Quick Start

[关键命令，≤10行]

## 模块索引

**进入任何目录前，先检查该目录是否有 AGENTS.md，有则先读。**

| 目录 | 职责 | 文件数 | 索引 |
|------|------|--------|------|
| `tools/` | 工具注册与执行 | 35 | [AGENTS.md](tools/AGENTS.md) |
| ... | ... | ... | ... |

[规模较小、不需拆分的模块直接在此列文件]

## 业务流程索引

[核心流程的入口→出口调用链，每条 ≤5行]
```

**PHASE_3_CHECKPOINT:**
- [ ] index-plan.json 已写入，包含所有 slot 分配
- [ ] root-agents-md-skeleton.md 已写入

---

## Success Criteria

- **DONE**: 两个文件均已写入，slot 分配合理（4个模块均有对应，文件数最大的优先）
