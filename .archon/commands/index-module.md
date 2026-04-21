---
description: 读取单个模块的 bash 扫描结果，深度阅读该模块代码，生成 AGENTS.md
argument-hint: <module_rel_path>
---

# index-module

**Goal**: 读 module-scan.json（bash 已算好），深度读该模块的代码，写 AGENTS.md（≤ 400 行）

**Context 预算原则**：只读这一个模块，不要跑到其他模块读文件。

---

## Phase 1: 读取扫描数据

```bash
cat $ARTIFACTS_DIR/module-scan.json
```

从中获取：
- `module_abs`: 模块绝对路径
- `module_rel`: 模块相对路径（如 `business/chat`）
- `total_src_lines`: 总行数
- `subdirs`: 子目录列表（已按行数排序，最大的在前）
- `direct_files`: 当前目录直接源码文件
- `has_existing_agents_md`: 是否已有 AGENTS.md

**PHASE_1_CHECKPOINT:**
- [ ] 已读取 module-scan.json
- [ ] 已记录 module_abs 和 total_src_lines

---

## Phase 2: 判断策略

**如果 total_src_lines ≤ 2000**：叶子节点，直接读代码写 AGENTS.md，覆盖所有维度。

**如果 total_src_lines > 2000 且有高内聚子目录**（子目录 src_lines 各自 > 2000）：
- 当前模块写「导航层」AGENTS.md（列出子模块入口，不深入代码细节）
- 子模块各自已有或将有独立 AGENTS.md

**如果 total_src_lines > 2000 但子目录行数分散/无内聚子域**：
- 读最关键的核心文件（行数最大的 top 5），写完整 AGENTS.md
- 不需要覆盖每一个文件，聚焦核心业务逻辑

---

## Phase 3: 深度阅读（只读本模块）

### 3.1 读核心文件

优先读（按顺序）：
1. 现有 `AGENTS.md`（如有，完整读取，了解已有索引）
2. `__init__.py` / `index.ts` / 模块 API 接口文件
3. 文件名含 `ViewModel`、`Repository`、`Service`、`Manager`、`Fragment`、`Activity` 的（各读前 80 行）
4. direct_files 中行数最大的 top 3（各读前 60 行）

### 3.2 Android 项目额外读

```bash
# 读模块的 build.gradle（了解依赖关系）
cat $MODULE_ABS/impl/build.gradle 2>/dev/null || cat $MODULE_ABS/build.gradle 2>/dev/null | head -40

# 找 Fragment / Activity 入口
find $MODULE_ABS -name "*Fragment.kt" -o -name "*Activity.kt" \
  -not -path "*/build/*" | head -5
```

### 3.3 业务流读取

根据模块类型追踪一条核心业务链：
- **UI 模块（含 Fragment/Activity）**：用户操作 → ViewModel → Repository → 网络/DB
- **Service/Manager 模块**：调用入口 → 核心处理逻辑 → 输出
- **API 模块（接口定义）**：列出所有对外接口及职责

**PHASE_3_CHECKPOINT:**
- [ ] 已读至少 3 个核心文件
- [ ] 已理解主要业务流

---

## Phase 4: 生成 AGENTS.md

写入 `$MODULE_ABS/AGENTS.md`，**严格 ≤ 400 行**。

### 模板（Android 业务模块）

```markdown
# [模块名]

> [一句话：这个模块是什么，承担什么业务职责，属于哪个产品线（Talkie/星野/通用）]

## 目录结构

| 文件/目录 | 职责 |
|-----------|------|
| `api/` | 对外接口定义（其他模块通过此处调用）|
| `impl/` | 具体实现 |
| `impl/src/.../ui/` | UI 层（Fragment/Activity/View）|
| `impl/src/.../viewmodel/` | ViewModel 层 |
| `impl/src/.../repository/` | 数据层 |

## 功能模块

### [子功能1]
- **职责**：...
- **核心文件**：`XxxFragment.kt`, `XxxViewModel.kt`
- **入口**：通过 Router `weaver://xxx` 跳转

### [子功能2]
...

## 业务流

```
用户操作 → XxxFragment → XxxViewModel.doSomething()
  → XxxRepository.fetch() → NetworkManager → API
  → 结果通过 StateFlow 回到 UI
```

**关键调用链：**
- 进入页面：`Router.open("weaver://xxx")` → `XxxFragment.onViewCreated()`
- 核心操作：`XxxViewModel.action()` → `XxxRepository.xxx()` → 更新 `_uiState`

## 用户视角

- 用户打开 [页面名] → 触发 `XxxFragment` → 加载 [数据]
- 用户点击 [按钮] → `XxxViewModel.doXxx()` → [结果]

## 架构视角

- **层次**：业务层 / business/
- **依赖**：`common/network`（网络）、`common/im`（IM）、`business/npc/api`（NPC 接口）
- **被依赖**：被 `business/main` 通过 Router 调用
- **模式**：MVVM + Repository，Claymore DI 注入

## 对外接口

（位于 `api/` 模块）

- `XxxService` — 描述
- `XxxRouter` — 路由入口定义
```

### 导航层模板（strategy=split 的顶层模块）

```markdown
# [模块名]（导航层）

> [一句话说明这是个聚合目录，包含哪些子业务]

## 子模块索引

| 子模块 | 职责 | 源码规模 | 索引 |
|--------|------|---------|------|
| `chat/` | AI 聊天核心 | ~120K 行 | [AGENTS.md](chat/AGENTS.md) |
| `npc/` | NPC 系统 | ~80K 行 | [AGENTS.md](npc/AGENTS.md) |
| ... | ... | ... | ... |

## 模块间关系

[子模块之间的主要依赖关系，≤ 10 行]
```

---

## Phase 5: 验证写入

```bash
wc -l $MODULE_ABS/AGENTS.md
```

如果超过 400 行：
1. 压缩冗余描述（代码示例、重复说明）
2. 如仍超出，将细节移到子模块 AGENTS.md，当前文件变导航层

写入成功后输出：
```
DONE: $MODULE_REL/AGENTS.md (XXX lines)
```

---

## Success Criteria

- **DONE**: AGENTS.md 已写入，行数 ≤ 400
