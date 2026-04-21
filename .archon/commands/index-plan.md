---
description: 读取 bash 已统计好的 module-stats.json，规划拆分计划
argument-hint: <none>
---

# index-plan

**Goal**: 读 bash 已算好的数据，判断每个模块的拆分策略，输出 index-plan.json

**重要**：所有数据已在 module-stats.json 中备齐，**不需要自己跑任何 bash 或读源码文件**。

---

## Phase 1: 读取统计数据

```bash
cat $ARTIFACTS_DIR/module-stats.json
```

数据结构：
- `top_modules`: 顶层目录列表，含 `src_lines`（总行数）
- `existing_agents_md`: 已有的 AGENTS.md 及行数
- `language_distribution`: 语言分布
- `project_type_hints`: build.gradle / package.json 等项目类型线索

**PHASE_1_CHECKPOINT:**
- [ ] 已读取 module-stats.json
- [ ] 已了解项目语言和类型

---

## Phase 2: 判断每个顶层模块的拆分策略

对每个 `src_lines > 0` 的顶层模块，应用以下决策：

| 条件 | 决策 |
|------|------|
| src_lines ≤ 2000 | `leaf`: 叶子节点，一个 AGENTS.md 覆盖，不拆子模块 |
| src_lines > 2000，无明显子业务分层 | `single`: 独立 AGENTS.md，不拆 |
| src_lines > 2000，有高内聚子目录（如 `business/chat/`、`business/npc/`）| `split`: 顶层写导航 AGENTS.md，子模块各自独立处理 |

**判断「高内聚子目录」的线索**（仅凭目录名判断，不读代码）：
- 目录名是独立业务词（chat, npc, payment, login, feed 等）
- 顶层模块是 `business/`、`feature/`、`modules/` 这类聚合目录

对于 `split` 类型的模块，列出其子目录作为独立处理单元（子目录名已在 top_modules 的结构中可以推断）。

---

## Phase 3: 输出 index-plan.json

写入 `$ARTIFACTS_DIR/index-plan.json`：

```json
{
  "project_dir": "/abs/path/to/project",
  "project_type": "android|web|backend|...",
  "language": "Kotlin|TypeScript|...",
  "is_frontend": false,
  "modules": [
    {
      "rel": "app",
      "abs": "/abs/path/app",
      "src_lines": 3038,
      "strategy": "leaf",
      "agents_md": "app/AGENTS.md",
      "status": "pending",
      "priority": 1
    },
    {
      "rel": "business",
      "abs": "/abs/path/business",
      "src_lines": 453281,
      "strategy": "split",
      "agents_md": "business/AGENTS.md",
      "status": "pending",
      "priority": 2,
      "submodules": [
        {
          "rel": "business/chat",
          "abs": "/abs/path/business/chat",
          "agents_md": "business/chat/AGENTS.md",
          "status": "pending"
        },
        {
          "rel": "business/npc",
          "abs": "/abs/path/business/npc",
          "agents_md": "business/npc/AGENTS.md",
          "status": "pending"
        }
      ]
    },
    {
      "rel": "common",
      "abs": "/abs/path/common",
      "src_lines": 172867,
      "strategy": "split",
      "agents_md": "common/AGENTS.md",
      "status": "pending",
      "priority": 3,
      "submodules": []
    }
  ],
  "root_summary_modules": [
    {
      "rel": "hook",
      "src_lines": 106,
      "reason": "src_lines <= 2000，在根 AGENTS.md 直接描述"
    }
  ]
}
```

**priority 排序原则**：src_lines 越大优先级越高（核心模块先索引）。

**PHASE_3_CHECKPOINT:**
- [ ] 每个 src_lines > 0 的模块都有对应条目
- [ ] strategy 已判断（leaf/single/split）
- [ ] split 类型的 submodules 已列出
- [ ] status 全部为 "pending"

---

## Success Criteria

- **DONE**: index-plan.json 已写入，结构完整
