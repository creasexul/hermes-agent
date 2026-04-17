---
description: 生成渐进式导航索引 navigation.md，使 AI Agent 能用"我想做 X"直接索引到具体代码位置
argument-hint: <project_root>
---

# archon-generate-navigation

**Goal**: 生成 `docs/navigation.md` — 项目的"意图导航"，AI Agent 说出意图就能找到代码

**Input**: 所有已生成的模块文档 + 架构文档

---

## Phase 1: LOAD — 收集所有已生成文档

```bash
echo "=== Architecture Doc ===" && cat docs/architecture.md 2>/dev/null | head -50
echo "=== Module Docs ===" && ls docs/modules/ 2>/dev/null
echo "=== Module Doc Contents ===" 
for f in docs/modules/*.md; do
  echo "--- $f ---"
  head -20 "$f" 2>/dev/null
done
```

```bash
# 也扫描项目关键 README 获取功能点
cat README.md 2>/dev/null | head -60
```

**PHASE_1_CHECKPOINT:**
- [ ] 已读取架构文档
- [ ] 已读取所有模块文档摘要

---

## Phase 2: CATALOG — 整理所有"意图 → 代码"映射

对项目进行功能维度的梳理，按以下问题类型组织：

**A. "我想理解 X"类**（学习/探索意图）
**B. "我想修改 X"类**（开发意图）
**C. "我想调试 X"类**（排错意图）
**D. "我想扩展 X"类**（贡献意图）
**E. "我想找 X 在哪里"类**（定位意图）

对每个意图，映射到：
- 具体文档 + 节（渐进式：先看文档摘要，再按需深入）
- 具体文件 + 行范围（如果明确）
- 关键函数/类名

**PHASE_2_CHECKPOINT:**
- [ ] 覆盖了至少 20 个具体意图
- [ ] 每个意图都有明确的文档指向

---

## Phase 3: WRITE — 生成导航文档

生成 `docs/navigation.md`：

```markdown
# Navigation Index — [项目名]

> 告诉我你想做什么，我告诉你去哪里看代码。
> 
> 使用方法：Ctrl+F 搜索关键词 → 找到意图 → 点击文档链接 → 按需深入

---

## 🔍 理解项目

| 我想理解... | 先看这里 | 深入这里 |
|------------|---------|---------|
| 整体架构和数据流 | [architecture.md §Core Data Flow](architecture.md#core-data-flow) | AGENTS.md §AIAgent Class |
| 项目如何启动 | [architecture.md §Quick Start](architecture.md#quick-start-for-ai-agents) | `run_agent.py` line 1-50 |
| [意图 N] | [文档链接] | [具体文件] |

---

## 🔧 修改功能

| 我想修改... | 文档 | 关键文件 | 关键函数 |
|------------|------|---------|---------|
| AI 模型调用逻辑 | [modules/agent.md](modules/agent.md) | `run_agent.py` | `run_conversation()` |
| 工具的定义和注册 | [modules/tools.md](modules/tools.md) | `tools/registry.py` | `registry.register()` |
| [意图 N] | [文档链接] | [文件] | [函数] |

---

## 🐛 调试问题

| 症状 | 先检查 | 关键日志/变量 |
|------|--------|-------------|
| 工具调用失败 | `tools/registry.py` — handler 返回值格式 | `handle_function_call()` 返回 |
| 消息不发送 | `gateway/platforms/` — 对应平台 adapter | `send_message()` 实现 |
| [症状 N] | [检查点] | [调试线索] |

---

## ➕ 扩展功能

| 我想添加... | 步骤文档 | 参考示例 |
|------------|---------|---------|
| 新工具 | [AGENTS.md §Adding New Tools](../AGENTS.md#adding-new-tools) | `tools/file_tools.py` |
| 新平台 | [gateway/platforms/ADDING_A_PLATFORM.md](../gateway/platforms/ADDING_A_PLATFORM.md) | `gateway/platforms/telegram.py` |
| [功能 N] | [文档链接] | [示例文件] |

---

## 📁 文件定位

| 我在找... | 文件 | 说明 |
|----------|------|------|
| 配置默认值 | `hermes_cli/config.py` — `DEFAULT_CONFIG` | 所有配置项的默认值 |
| 系统提示词 | `agent/prompt_builder.py` | 系统 prompt 组装逻辑 |
| [我在找 N] | [文件路径] | [一句话说明] |

---

## 渐进式阅读路径

### 路径 A：新贡献者（2小时上手）
1. `README.md` — 5分钟了解项目
2. `AGENTS.md` §Project Structure — 10分钟建立目录直觉
3. `docs/architecture.md` — 20分钟理解数据流
4. 按你的任务找对应模块文档

### 路径 B：修 Bug（30分钟定位）
1. 从症状 → [调试问题] 表 → 找检查点
2. 进入对应模块文档的 §Common Pitfalls
3. 找具体函数，加断点

### 路径 C：加新功能（1小时上手）
1. `docs/architecture.md §Extension Points` — 找扩展点
2. [扩展功能] 表 → 找步骤文档
3. 看参考示例实现

---

*最后更新: [日期] | 由 archon-document-codebase workflow 自动生成*
```

将文档写入 `docs/navigation.md`。

**PHASE_3_CHECKPOINT:**
- [ ] docs/navigation.md 已创建
- [ ] 包含理解/修改/调试/扩展四类意图表
- [ ] 包含渐进式阅读路径
- [ ] 所有链接格式正确（相对路径）

---

## Success Criteria

- **DONE**: navigation.md 存在，随便给一个开发意图，都能从表中找到对应的文档和文件
