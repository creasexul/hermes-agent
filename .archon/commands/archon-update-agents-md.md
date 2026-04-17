---
description: 更新 AGENTS.md，插入文档层级引用，使顶层文档成为导航入口
argument-hint: <project_root>
---

# archon-update-agents-md

**Goal**: 在现有 AGENTS.md 基础上，插入文档层级导航，不破坏现有内容

**Input**: 
- 现有 `AGENTS.md`
- 已生成的 `docs/architecture.md`、`docs/navigation.md`、`docs/modules/*.md`

---

## Phase 1: LOAD — 读取现状

```bash
cat AGENTS.md
```

```bash
ls docs/ docs/modules/ 2>/dev/null
```

```bash
# 检查现有 AGENTS.md 中是否已有导航节
grep -n "navigation\|docs/\|渐进式\|Navigation" AGENTS.md | head -20
```

**PHASE_1_CHECKPOINT:**
- [ ] 已读取完整 AGENTS.md
- [ ] 已知道现有文档目录结构

---

## Phase 2: PLAN — 规划插入点

分析 AGENTS.md 结构，确定：

1. **文档导航节插入位置**: 建议插在第一个 `##` 节之前（最顶部）或 Project Structure 节之后
2. **模块文档交叉引用**: 在 AGENTS.md 中提到某个模块时，加上对应 docs/modules/ 的链接
3. **不破坏原则**: 只增加，不删除；只在节末尾加链接，不修改原有内容

**PHASE_2_CHECKPOINT:**
- [ ] 已确定插入位置
- [ ] 已列出需要加交叉引用的位置

---

## Phase 3: UPDATE — 更新 AGENTS.md

### 3.1 在文件开头（Project Structure 之前）插入文档导航节

```markdown
## 📚 Documentation Hub

> **快速导航** — 根据你的意图直接跳转：

| 我想... | 去这里 |
|--------|-------|
| 理解整体架构和数据流 | [docs/architecture.md](docs/architecture.md) |
| 根据意图找到对应代码 | [docs/navigation.md](docs/navigation.md) — 意图导航索引 |
| 深入了解某个模块 | [docs/modules/](docs/modules/) — 各模块详细文档 |
| 添加新工具/平台/功能 | 见下方各节 + [docs/navigation.md §扩展功能](docs/navigation.md) |

**渐进式阅读顺序**: README → 本文件 §Project Structure → [architecture.md](docs/architecture.md) → 按需查 [modules/](docs/modules/)
```

### 3.2 在 Project Structure 节内，为各目录加文档链接

找到类似以下格式的内容：
```
├── tools/                # Tool implementations
├── agent/                # Agent internals
├── gateway/              # Messaging platform gateway
```

在有对应模块文档的目录旁加链接：
```
├── tools/                # Tool implementations → [📖 docs/modules/tools.md](docs/modules/tools.md)
├── agent/                # Agent internals → [📖 docs/modules/agent.md](docs/modules/agent.md)
```

### 3.3 在关键节末尾加 "深入阅读" 链接

在 "Adding New Tools"、"AIAgent Class" 等节末尾加：
```markdown
> 📖 **深入阅读**: [docs/modules/tools.md](docs/modules/tools.md) — 工具系统完整参考
```

### 3.4 执行更新

用 Python 脚本精确插入，避免意外破坏文件：

```python
#!/usr/bin/env python3
import re

with open('AGENTS.md', 'r') as f:
    content = f.read()

# 在第一个 ## 之前插入文档导航节
doc_hub = """## 📚 Documentation Hub

> **快速导航** — 根据你的意图直接跳转：

| 我想... | 去这里 |
|--------|-------|
| 理解整体架构和数据流 | [docs/architecture.md](docs/architecture.md) |
| 根据意图找到对应代码 | [docs/navigation.md](docs/navigation.md) |
| 深入了解某个模块 | [docs/modules/](docs/modules/) |

**渐进式阅读**: README → AGENTS.md §Project Structure → [architecture.md](docs/architecture.md) → 按需查 [modules/](docs/modules/)

---

"""

# 只在没有 Documentation Hub 的情况下插入
if '## 📚 Documentation Hub' not in content:
    # 在第一个 ## 前插入
    content = re.sub(r'^(## )', doc_hub + r'\1', content, count=1, flags=re.MULTILINE)
    with open('AGENTS.md', 'w') as f:
        f.write(content)
    print("✅ Documentation Hub inserted")
else:
    print("ℹ️ Documentation Hub already exists, skipping")
```

**PHASE_3_CHECKPOINT:**
- [ ] Documentation Hub 节已插入
- [ ] 关键模块目录已加文档链接
- [ ] 原有内容未被破坏

---

## Phase 4: VERIFY — 验证更新

```bash
head -50 AGENTS.md
grep -n "docs/\|📚\|📖" AGENTS.md | head -20
```

确认：
1. Documentation Hub 在文件顶部可见
2. 所有文档链接格式正确（相对路径）
3. 原有结构完整

**PHASE_4_CHECKPOINT:**
- [ ] AGENTS.md 顶部有 Documentation Hub
- [ ] grep 能找到 docs/ 链接
- [ ] 文件结构完整

---

## Success Criteria

- **DONE**: AGENTS.md 已更新，AI Agent 读到文件开头就能通过 Documentation Hub 导航到任意文档
