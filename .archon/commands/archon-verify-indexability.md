---
description: 验证文档体系是否达到"任意代码均可被索引"的标准，输出覆盖率报告和待办清单
argument-hint: <project_root>
---

# archon-verify-indexability

**Goal**: 验证文档体系的「渐进式可索引性」，生成覆盖率报告

**标准**: 给定任意开发意图，AI Agent 应能在 3 步内（导航 → 模块文档 → 代码）找到目标

---

## Phase 1: INVENTORY — 盘点文档体系

```bash
echo "=== 文档文件清单 ==="
find docs/ -name "*.md" 2>/dev/null | sort

echo "=== 模块清单 ==="
find . -maxdepth 1 -type d | grep -v "^\.$\|\.git\|__pycache__\|venv\|\.venv\|node_modules\|tests\|optional-skills\|docs\|\.archon"

echo "=== 核心 Python 文件数 ==="
find . -maxdepth 2 -name "*.py" -not -path "*/__pycache__/*" -not -path "*/test*" -not -path "*/.venv/*" | wc -l
```

```bash
echo "=== AGENTS.md Documentation Hub 检查 ==="
grep -n "Documentation Hub\|docs/architecture\|docs/navigation\|docs/modules" AGENTS.md | head -20

echo "=== navigation.md 意图数量 ==="
grep -c "^|" docs/navigation.md 2>/dev/null || echo "navigation.md not found"

echo "=== architecture.md 关键节 ==="
grep "^##" docs/architecture.md 2>/dev/null || echo "architecture.md not found"

echo "=== module docs 列表 ==="
ls docs/modules/ 2>/dev/null || echo "docs/modules/ not found"
```

**PHASE_1_CHECKPOINT:**
- [ ] 已获取所有文档文件清单
- [ ] 已获取模块清单

---

## Phase 2: TEST — 意图测试（随机抽样）

从以下意图清单中各选1个，测试是否能在 ≤3 步找到代码：

**测试集 A — 理解类**:
1. "我想理解消息从用户发出到 AI 响应的完整路径"
2. "我想理解工具是如何注册和调用的"
3. "我想理解 session 如何持久化"

**测试集 B — 修改类**:
4. "我想给工具系统添加一个新的 web_scraper 工具"
5. "我想修改系统提示词的组装逻辑"
6. "我想给 gateway 添加一个新的消息平台"

**测试集 C — 调试类**:
7. "工具调用返回错误，我从哪里开始调试"
8. "消息没有发送到飞书，我检查什么"

对每个意图，执行以下步骤并记录路径：

```
Step 1: docs/navigation.md 中能找到吗？
  → 是：记录找到的条目 → 进 Step 2
  → 否：记录 MISS，这是一个文档 gap

Step 2: 跳转到指向的文档，30秒内找到相关节了吗？
  → 是：记录找到的节 → 进 Step 3
  → 否：记录 MISS

Step 3: 文档中有具体的文件路径或函数名吗？
  → 是：记录 HIT，测试通过
  → 否：记录 MISS
```

**PHASE_2_CHECKPOINT:**
- [ ] 完成 8 个意图测试
- [ ] 记录每个测试的结果（HIT/MISS + 路径）

---

## Phase 3: REPORT — 生成验证报告

生成 `docs/indexability-report.md`：

```markdown
# Indexability Report — [项目名]
*生成时间: [日期]*

## 总体评分

| 指标 | 结果 | 状态 |
|------|------|------|
| 文档文件数 | N 个 | ✅/⚠️ |
| 核心模块覆盖率 | N/M 个模块有文档 | ✅/⚠️ |
| 意图测试通过率 | N/8 | ✅/⚠️ |
| 渐进式路径最大步数 | N 步 | ✅(≤3)/❌(>3) |

**综合评级**: A（优秀）/ B（良好）/ C（需改进）/ D（严重不足）

---

## 意图测试结果

| 意图 | Step 1 (navigation) | Step 2 (文档节) | Step 3 (代码定位) | 结论 |
|------|--------------------|-----------------|--------------------|------|
| 理解消息路径 | ✅ 找到 | ✅ §Data Flow | ✅ run_agent.py:L80 | HIT |
| 添加新工具 | ✅ 找到 | ✅ §Extension | ✅ tools/registry.py | HIT |
| [意图 N] | ✅/❌ | ✅/❌ | ✅/❌ | HIT/MISS |

---

## 文档覆盖 Gap（待补全）

| 模块 | 当前状态 | 优先级 | 待办 |
|------|---------|--------|------|
| `hermes_cli/` | 无文档 | HIGH | 生成 docs/modules/cli.md |
| `cron/` | 无文档 | MEDIUM | 生成 docs/modules/cron.md |
| [模块 N] | [状态] | [优先级] | [具体任务] |

---

## 下一步优化建议

1. **立即**: [最关键的 1-2 个 gap]
2. **本周**: [次优先级]
3. **长期**: [完善整个体系]

---

## 如何复用此 Workflow 到其他项目

```bash
# 在任意项目根目录
cd ~/your-project
mkdir -p .archon/workflows .archon/commands

# 复制 workflow 和 command 文件
cp ~/.hermes/hermes-agent/.archon/workflows/archon-document-codebase.yaml .archon/workflows/
cp ~/.hermes/hermes-agent/.archon/commands/archon-generate-*.md .archon/commands/
cp ~/.hermes/hermes-agent/.archon/commands/archon-update-agents-md.md .archon/commands/
cp ~/.hermes/hermes-agent/.archon/commands/archon-verify-indexability.md .archon/commands/

# 运行
archon workflow run archon-document-codebase .
```

**预期耗时**: 30-60 分钟（取决于项目大小和模块数量）
**产出**: architecture.md + navigation.md + modules/*.md + 更新的 AGENTS.md
```

将报告写入 `docs/indexability-report.md`。

**PHASE_3_CHECKPOINT:**
- [ ] docs/indexability-report.md 已生成
- [ ] 报告包含总体评分
- [ ] 报告包含意图测试结果表
- [ ] 报告包含具体 gap 清单
- [ ] 报告包含复用指南

---

## Phase 4: SUMMARY — 输出执行摘要

最后在终端输出简洁摘要（同时写到 $ARTIFACTS_DIR/summary.txt）：

```
📊 hermes-agent 文档化完成报告
================================
✅ 新增文档: docs/architecture.md
✅ 新增文档: docs/navigation.md  
✅ 新增文档: docs/modules/*.md (N 个)
✅ 更新: AGENTS.md (加入 Documentation Hub)
✅ 验证报告: docs/indexability-report.md

📈 可索引性评分: [A/B/C/D]
📋 意图测试通过率: N/8

🔴 待补全 (HIGH): [模块列表]
🟡 待补全 (MEDIUM): [模块列表]

复用命令:
  cp -r .archon/ ~/other-project/ && cd ~/other-project && archon workflow run archon-document-codebase .
```

---

## Success Criteria

- **DONE**: indexability-report.md 已生成，包含评分、测试结果、gap 清单和复用指南
