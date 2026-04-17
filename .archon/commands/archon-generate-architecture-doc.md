---
description: 生成项目架构总览文档，包含数据流 ASCII 图、模块依赖关系、核心设计决策
argument-hint: <artifacts_dir>
---

# archon-generate-architecture-doc

**Goal**: 根据已扫描的项目结构和代码，生成 `docs/architecture.md`

**Input**: 
- `$ARTIFACTS_DIR/scan-structure.txt` — 项目扫描结果
- `$ARTIFACTS_DIR/read-critical-modules.txt` — 关键模块源码摘要

---

## Phase 1: LOAD — 读取扫描数据

```bash
cat $ARTIFACTS_DIR/scan-structure.txt 2>/dev/null || echo "scan not found, reading from context"
cat $ARTIFACTS_DIR/read-critical-modules.txt 2>/dev/null | head -200
```

也读取现有 AGENTS.md（如果存在）：
```bash
cat AGENTS.md 2>/dev/null | head -100
```

读取核心入口文件：
```bash
# 找项目入口点
find . -maxdepth 2 -name "main.py" -o -name "run_agent.py" -o -name "cli.py" -o -name "index.ts" -o -name "app.ts" | head -5
```

对于找到的入口文件，读取前100行：
```bash
# 对每个入口文件执行
head -100 <entry_file>
```

**PHASE_1_CHECKPOINT:**
- [ ] 已获取项目结构数据
- [ ] 已读取至少1个入口文件

---

## Phase 2: ANALYZE — 理解架构

深度分析以下维度：

1. **项目定位**: 这是什么？解决什么问题？
2. **核心数据流**: 请求从哪里进来，经过哪些模块，从哪里出去？
3. **模块依赖关系**: 哪些模块依赖哪些？有没有循环依赖？
4. **关键设计决策**: 为什么这样组织代码？有什么约束？
5. **扩展点**: 开发者在哪里扩展功能？

**PHASE_2_CHECKPOINT:**
- [ ] 已识别核心数据流路径
- [ ] 已识别至少3个核心模块

---

## Phase 3: WRITE — 生成架构文档

生成 `docs/architecture.md`，格式严格按照以下结构：

```markdown
# Architecture — [项目名]

> **一句话**: [项目是什么，给 AI Agent 的简洁定位]

## Quick Start for AI Agents

**想理解入口点** → 看 [入口文件] — [一句话描述]
**想理解数据流** → 看下方 [Core Data Flow] 节
**想找某个功能** → 看 [docs/navigation.md](navigation.md)
**想修改某个模块** → 看 [docs/modules/MODULE_NAME.md](modules/)

---

## Core Data Flow

```
[输入层]                [处理层]              [输出层]
用户输入 ──────────▶  核心模块 A  ─────────▶  结果输出
                         │
                         ▼
                      模块 B (存储/工具)
```

[用2-3段文字解释这个流程]

---

## Module Map

| 目录/文件 | 职责 | 文档 | 关键类/函数 |
|-----------|------|------|-------------|
| `module_a/` | [职责描述] | [docs/modules/module_a.md](modules/module_a.md) | `ClassName`, `fn_name()` |
| `module_b.py` | [职责描述] | — | `fn_name()` |

---

## Dependency Graph

```
[高层组件]
     │
     ├── [子模块 A]
     │       └── [依赖 X]
     └── [子模块 B]
             └── [依赖 Y]
```

---

## Key Design Decisions

### 1. [决策名称]
**Why**: [原因]
**Impact**: [影响哪些模块]
**Note for AI Agents**: [AI 在修改时需要注意什么]

### 2. [决策名称]
...

---

## Extension Points

| 扩展场景 | 在哪里扩展 | 参考示例 |
|---------|-----------|---------|
| 添加新工具 | `tools/` — 实现 registry.register() | `tools/file_tools.py` |
| 添加新平台 | `gateway/platforms/` — 实现 IPlatformAdapter | `gateway/platforms/telegram.py` |
```

```bash
mkdir -p docs/modules
```

将生成的内容写入 `docs/architecture.md`。

**PHASE_3_CHECKPOINT:**
- [ ] docs/architecture.md 已创建
- [ ] 包含 Core Data Flow ASCII 图
- [ ] 包含 Module Map 表格
- [ ] 包含 Quick Start for AI Agents 节

---

## Success Criteria

- **DONE**: `docs/architecture.md` 存在，且 AI Agent 读完后能立即定位到任意核心模块
- **质量标准**: Quick Start 节中每个问题都有明确的答案和指向
