---
description: 深度探索单个高内聚模块，生成该模块的 AGENTS.md（含业务流程维度）
argument-hint: <module_slot_number>
---

# archon-explore-module

**Goal**: 读取分配给本节点的模块，深度探索代码，生成高质量 AGENTS.md

**输入**:
- `$ARTIFACTS_DIR/index-plan.json` — 规划文件，含 slot 分配
- `$ARTIFACTS_DIR/module_slot.txt` — 本节点的 slot 编号

---

## Phase 1: 领取任务

```bash
cat $ARTIFACTS_DIR/module_slot.txt
cat $ARTIFACTS_DIR/index-plan.json
```

根据 slot 编号，从 `index_tree` 中找到对应的模块条目，记录：
- `path`: 模块目录
- `agents_md_path`: 要写入的目标文件
- `description`: 模块描述
- `needs_subindex`: 是否需要继续拆子层
- `extra_dimensions`: 除文件索引外，还需要补充哪些维度

如果 slot 编号超过 index_tree 长度（模块不足4个），输出 "NO_MODULE_FOR_SLOT" 并退出。

**PHASE_1_CHECKPOINT:**
- [ ] 已确认本节点负责的模块路径
- [ ] 已知道需要补充哪些额外维度

---

## Phase 2: 深度探索模块

### 2.1 目录结构扫描

```bash
find <module_path> -not -path "*/__pycache__/*" -not -path "*/build/*" \
  -not -name "*.pyc" -not -name "*.class" \
  | sort | head -100
```

```bash
# 统计各子目录文件数
find <module_path> -maxdepth 2 -type d | while read d; do
  count=$(find "$d" -maxdepth 1 -name "*.py" -o -name "*.ts" -o -name "*.kt" 2>/dev/null | wc -l)
  [ "$count" -gt 0 ] && echo "$count  $(basename $d)"
done | sort -rn
```

### 2.2 读核心文件

优先读：
1. `__init__.py` / `index.ts` / `mod.rs` — 模块对外暴露的接口
2. `registry.py` / `router.ts` / `handler.py` — 注册/分发中心
3. 文件名最能体现模块入口的文件（如 `run.py`, `executor.ts`）

```bash
# 每个核心文件读前 80 行（超长文件不全读）
head -80 <core_file>
```

### 2.3 追踪业务流程（关键！）

根据 `extra_dimensions` 中的流程描述，主动追踪：

**例：「工具调用链」**
```bash
# 找 register 调用点
grep -n "registry.register\|@register\|def register" <module_path> -r | head -20
# 找 dispatch/handle 调用点  
grep -n "handle_function_call\|dispatch\|execute" <module_path> -r | head -20
# 找典型工具文件，读完整流程
head -50 <typical_tool_file>
```

**例：「消息接收→处理→发送链路」**
```bash
# 找消息入口
grep -rn "on_message\|receive\|webhook\|handle_message" <module_path> | head -20
# 找发送出口
grep -rn "send_message\|reply\|respond" <module_path> | head -20
```

**例：「slash command 分发链」**
```bash
grep -rn "COMMAND_REGISTRY\|CommandDef\|process_command\|resolve_command" <module_path> | head -20
```

继续读追踪到的关键文件，直到能写出完整的「调用链 A→B→C→D」。

### 2.4 检查是否有已存在的子模块 AGENTS.md

```bash
find <module_path> -name "AGENTS.md" | head -10
```

如果已有子层 AGENTS.md，读取它们作为参考。

**PHASE_2_CHECKPOINT:**
- [ ] 已扫描目录结构，知道每个子目录/文件的职责
- [ ] 已读至少 3 个核心文件
- [ ] 已追踪出至少 1 条完整业务调用链

---

## Phase 3: 判断是否需要子层索引

根据探索结果，判断：

**需要子层（在本 AGENTS.md 中列链接）**：
- 子域文件数 > 30，且职责高度内聚（如 gateway/platforms/ 下每个平台独立）
- 存在 3+ 个明确的子业务域，每个域有清晰边界

**不需要子层（直接在本 AGENTS.md 列文件表）**：
- 所有文件都在一个职责域内
- 文件数 < 30，一个表格能说清楚

如果需要子层，**递归执行**：对每个子域，重复 Phase 2 的探索，然后在 `<submodule>/AGENTS.md` 写出子层索引（格式同本文件，但更具体）。

---

## Phase 4: 写出 AGENTS.md

按以下模板写入 `<agents_md_path>`（即 `<module_path>/AGENTS.md`）：

```markdown
# <模块名> — <一句话职责>

<2-3句话：这个模块做什么，不做什么，谁调用它>

## 文件索引

<根据内容选择合适的组织方式：>

**方式 A — 简单平铺（文件数 < 20，无明显子域）**：

| 文件 | 职责 | 关键类/函数 |
|------|------|-------------|
| `registry.py` | 工具注册中心，所有工具在此注册和 dispatch | `registry.register()`, `handle()` |
| `file_tools.py` | 文件读写搜索工具 | `read_file()`, `search_files()`, `patch()` |

**方式 B — 分组（有 2-4 个子域，每组 < 10 个文件）**：

### 核心调度
| 文件 | 职责 |
|------|------|
| `registry.py` | 注册中心 |

### 具体工具
| 文件 | 职责 |
|------|------|
| `file_tools.py` | 文件操作 |

**方式 C — 子层链接（有 3+ 大型内聚子域）**：

| 子模块 | 文件数 | 职责 | 索引 |
|--------|--------|------|------|
| `platforms/` | 25 | 各平台 adapter | [AGENTS.md](platforms/AGENTS.md) |

## <业务流程维度（根据 extra_dimensions 按需添加）>

### 工具调用链
```
用户输入 → run_agent.py:handle_function_call()
  → tools/registry.py:registry.dispatch(tool_name, args)
  → tools/<tool_file>.py:<handler_fn>(args)
  → 返回 JSON string → 写入 messages
```
关键约束：所有 handler **必须返回 JSON string**

### 新增工具步骤
1. 创建 `tools/<your_tool>.py`，调用 `registry.register()`
2. 在 `model_tools.py` 的 `_discover_tools()` 中 import
3. 在 `toolsets.py` 中加入对应 toolset

<其他流程维度...>

## 相关文档

- [父层索引](../AGENTS.md)
- [架构总览](../docs/architecture.md)（如有）
```

**格式硬约束**：
- 总行数 ≤ 120 行（叶子层），≤ 80 行（中间层）
- 每个文件条目一行，不展开内容
- 业务流程用箭头链写，≤ 8 行/条流程
- 不写"详细说明"、不写实现细节、不写代码示例

写入文件：
```bash
# 确保目录存在
mkdir -p $(dirname <agents_md_path>)
# 写入
cat > <agents_md_path> << 'AGENTS_CONTENT'
...内容...
AGENTS_CONTENT
```

**PHASE_4_CHECKPOINT:**
- [ ] AGENTS.md 已写入磁盘
- [ ] 总行数在约束范围内
- [ ] 包含文件索引 + 至少 1 条业务流程
- [ ] 如需子层，子层 AGENTS.md 也已写入

---

## Success Criteria

- **DONE**: `<agents_md_path>` 存在，内容满足：AI 读完本文件后，能在 ≤2 次额外 Read 内定位到任意具体代码位置
