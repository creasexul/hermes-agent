---
description: 汇总各模块索引结果，生成项目根 AGENTS.md
argument-hint: <none>
---

# index-write-root

**Goal**: 读取各模块完成报告和 index-plan.json，生成项目根 AGENTS.md

---

## Phase 1: 收集各模块完成情况

```bash
cat "$ARTIFACTS_DIR/index-plan.json"

for i in 1 2 3 4; do
  f="$ARTIFACTS_DIR/module-${i}-done.json"
  [ -f "$f" ] && echo "=== slot $i ===" && cat "$f"
done
```

同时读取已生成的各模块 AGENTS.md 的前 10 行，了解各模块索引的概要。

## Phase 2: 读取项目基本信息

```bash
# 从 index-plan.json 获取
# project_name, project_dir, project_type, language, framework
# root_summary_modules（不拆分的小模块，需在根 AGENTS.md 直接描述）

# 读现有根 AGENTS.md（如有，了解已有内容）
PROJECT_DIR=$(cat "$ARTIFACTS_DIR/index-plan.json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['project_dir'])")
cat "$PROJECT_DIR/AGENTS.md" 2>/dev/null || echo "NO EXISTING ROOT AGENTS.MD"

# 读 README（获取 Quick Start 信息）
head -100 "$PROJECT_DIR/README.md" 2>/dev/null || echo "NO README"
```

对 `root_summary_modules` 中的每个小模块，快速读取其核心文件（≤ 3 个文件，各 40 行），理解其职责，在根 AGENTS.md 中直接描述（不单独建索引文件）。

## Phase 3: 生成根 AGENTS.md

写入 `$PROJECT_DIR/AGENTS.md`，严格控制在 400 行以内。

### 根 AGENTS.md 模板

```markdown
# [项目名]

> [一句话：项目是什么、做什么、面向谁]

## Quick Start

```bash
# 最关键的启动/运行命令，≤ 10 行
```

## 模块索引

> 进入任何子目录前，先检查该目录是否有 AGENTS.md，有则先读。

| 目录 | 职责 | 源码规模 | 索引 |
|------|------|---------|------|
| `tools/` | 工具注册与执行系统 | ~4200 行 | [AGENTS.md](tools/AGENTS.md) |
| `gateway/` | 消息平台网关（18个平台适配器）| ~5800 行 | [AGENTS.md](gateway/AGENTS.md) |
| ... | ... | ... | ... |

## 不拆分模块说明

[对 root_summary_modules 中每个小模块，直接在此描述]

### `cron/` — 定时任务调度
- **职责**：...
- **关键文件**：`jobs.py`（CRUD）、`scheduler.py`（执行）
- **核心接口**：...

## 项目整体业务流

[项目最核心的 1-3 条端到端业务流，每条用简短调用链描述]

**流程1: [名称]**
```
用户输入 → [入口模块] → [处理模块] → [输出]
```

**流程2: [名称]**
...

## 架构概览

- **架构风格**：[分层架构/微服务/事件驱动/...]
- **核心依赖关系**：
  ```
  模块A → 模块B（用途）
  模块C → 模块A（用途）
  ```
- **关键设计决策**：[影响全局的设计决策，≤ 5 条]

## 根目录关键文件

| 文件 | 职责 |
|------|------|
| `main.py` | 项目入口 |
| `config.yaml` | 全局配置 |
| ... | ... |
```

**PHASE_3_CHECKPOINT:**
- [ ] 模块索引表完整（所有已建 AGENTS.md 的模块都列出）
- [ ] root_summary_modules 全部有描述
- [ ] 业务流覆盖核心场景
- [ ] 行数 ≤ 400

## Phase 4: 写入并确认

```bash
wc -l "$PROJECT_DIR/AGENTS.md"
echo "Root AGENTS.md written."
```

---

## Success Criteria

- **DONE**: 根 AGENTS.md 已写入，结构完整，行数 ≤ 400
