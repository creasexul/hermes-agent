---
description: 用真实意图测试验证索引体系可索引性，输出评分和 gap 清单
argument-hint: <project_root>
---

# archon-review-indexability

**Goal**: 用模拟真实开发意图的测试验证索引质量，输出结构化 gap 报告

**评分标准**: 给定任意开发意图，能在 ≤3 次 Read 内定位到具体代码 → 通过

---

## Phase 1: 盘点现有索引文件

```bash
find . -name "AGENTS.md" \
  -not -path "*/.git/*" \
  -not -path "*/build/*" \
  -not -path "*/optional-skills/*" \
  | sort | while read f; do
    echo "$(wc -l < $f) lines  $f"
  done
```

```bash
# 读根 AGENTS.md（测试起点）
cat AGENTS.md
```

**PHASE_1_CHECKPOINT:**
- [ ] 已列出所有 AGENTS.md
- [ ] 已读根 AGENTS.md

---

## Phase 2: 意图测试（逐一执行，记录路径）

针对以下每个意图，**真实模拟** Claude Code 的检索行为（实际执行 Read/cat 命令），记录每步：

### 测试集 A — 代码定位类

**A1**: "我想修改 AI 模型调用逻辑，找 run_conversation() 的实现"
```bash
# Step 1: 读根 AGENTS.md → 找到入口提示
# Step 2: 按提示 read → 找到函数
# 记录：用了几步？最终定位到哪个文件哪一行？
grep -n "run_conversation" run_agent.py | head -5
```

**A2**: "我想给工具系统加一个新工具 web_scraper"
```bash
# 从根 AGENTS.md 找 tools/ 入口
cat tools/AGENTS.md 2>/dev/null || echo "MISS: tools/AGENTS.md 不存在"
# 验证：AGENTS.md 中是否有「新增工具步骤」
grep -n "新增\|步骤\|Step\|register" tools/AGENTS.md 2>/dev/null | head -10
```

**A3**: "我想调试 Telegram 消息没有发出去的问题"
```bash
cat gateway/AGENTS.md 2>/dev/null || echo "MISS"
cat gateway/platforms/AGENTS.md 2>/dev/null | head -20 || echo "MISS"
grep -rn "send_message\|reply" gateway/platforms/ | head -10
```

**A4**: "我想修改 slash command /model 的处理逻辑"
```bash
cat hermes_cli/AGENTS.md 2>/dev/null || echo "MISS"
grep -n "model\|/model\|slash" hermes_cli/AGENTS.md 2>/dev/null | head -10
```

**A5**: "我想理解 context window 压缩是怎么工作的"
```bash
cat agent/AGENTS.md 2>/dev/null || echo "MISS"
grep -n "compress\|compressor\|context" agent/AGENTS.md 2>/dev/null | head -10
```

### 测试集 B — 业务流程类

**B1**: "用户发消息到 Telegram，到 AI 回复，完整链路是什么？"
```bash
# 验证根 AGENTS.md 中有业务流程索引
grep -n "Telegram\|消息.*链\|chain\|flow" AGENTS.md | head -10
```

**B2**: "定时任务是怎么触发的？"
```bash
grep -n "cron\|scheduler\|定时" AGENTS.md | head -5
cat cron/AGENTS.md 2>/dev/null || grep -n "cron" AGENTS.md
```

### 测试集 C — 扩展类

**C1**: "我想给 gateway 加一个新的 WhatsApp 平台"
```bash
cat gateway/AGENTS.md 2>/dev/null | grep -A3 "platform\|新增\|添加" | head -15
cat gateway/platforms/AGENTS.md 2>/dev/null | head -20
```

**C2**: "我想加一个新的 CLI slash command /export"
```bash
cat hermes_cli/AGENTS.md 2>/dev/null | grep -A5 "command\|新增\|CommandDef" | head -20
```

**PHASE_2_CHECKPOINT:**
- [ ] 已执行所有 7 个意图测试
- [ ] 记录了每个测试的步骤数和结果（HIT/MISS）

---

## Phase 3: 评分和输出报告

统计测试结果，计算评分：

- 每个意图 ≤3 步定位到代码 = HIT（1分）
- 超过3步或找不到 = MISS（0分）
- 总分 = HIT数 / 总测试数 × 10（满分10）

写入 `$ARTIFACTS_DIR/review-indexability-result.json`：

```json
{
  "score": 7.5,
  "total_tests": 9,
  "hits": 7,
  "misses": 2,
  "results": [
    {
      "test": "A1",
      "intent": "修改 run_conversation() 实现",
      "result": "HIT",
      "steps": 2,
      "path": "AGENTS.md → run_agent.py:L80"
    },
    {
      "test": "A2",
      "intent": "添加新工具",
      "result": "MISS",
      "steps": 3,
      "issue": "tools/AGENTS.md 存在但缺少「新增工具步骤」流程描述"
    }
  ],
  "gaps": [
    {
      "module": "tools/",
      "agents_md": "tools/AGENTS.md",
      "missing_dimension": "新增工具的步骤流程",
      "failed_tests": ["A2"],
      "fix": "在 tools/AGENTS.md 中补充 §新增工具步骤，列出 register→import→toolset 三步"
    },
    {
      "module": "hermes_cli/",
      "agents_md": "hermes_cli/AGENTS.md",
      "missing_dimension": "slash command 分发链和新增步骤",
      "failed_tests": ["A4", "C2"],
      "fix": "补充 CommandDef → resolve_command() → process_command() 调用链，以及新增 command 的3步流程"
    }
  ]
}
```

同时在终端输出简洁摘要：

```
📊 索引可索引性评审
===================
总分: X/10  (Y/9 意图通过)

✅ HIT: A1 A3 A5 B1 B2 C1
❌ MISS:
  - A2: tools/AGENTS.md 缺少「新增工具」流程
  - C2: hermes_cli/AGENTS.md 缺少 slash command 扩展指引

→ 需要 patch-gaps 循环修复 2 个 gap
```

**PHASE_3_CHECKPOINT:**
- [ ] review-indexability-result.json 已写入
- [ ] score、gaps 字段完整
- [ ] 每个 gap 有明确的 fix 描述

---

## Success Criteria

- **DONE**: 报告已生成，score ≥ 8 则索引体系达标；score < 8 则 patch-gaps 循环会读取 gaps 修复
