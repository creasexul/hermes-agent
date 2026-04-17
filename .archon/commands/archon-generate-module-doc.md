---
description: 为单个模块生成深度文档，包含 API 参考、数据流、扩展指南
argument-hint: <module_name>
---

# archon-generate-module-doc

**Goal**: 为一个核心模块生成 `docs/modules/<module_name>.md`

**Input**:
- `$ARTIFACTS_DIR/analyze-gaps.json` — gap 分析结果（含模块列表和优先级）
- `$ARTIFACTS_DIR/module_index_N.txt` — 当前节点处理第 N 个模块（N=1/2/3）

---

## Phase 1: LOAD — 确定要处理哪个模块

```bash
# 读取模块索引
cat $ARTIFACTS_DIR/module_index_*.txt 2>/dev/null | head -1
```

```bash
# 读取 gap 分析
cat $ARTIFACTS_DIR/analyze-gaps.json 2>/dev/null || echo "{}"
```

根据索引 N，选取 gap_modules 数组中第 N 个 priority=critical 或 priority=high 的模块。

如果 gap 分析文件不存在，通过以下方式自动发现：
```bash
# 找最重要但没有文档的目录
find . -maxdepth 1 -type d | grep -v "^\.$\|\.git\|__pycache__\|venv\|\.venv\|node_modules\|tests\|optional-skills"
```

**PHASE_1_CHECKPOINT:**
- [ ] 已确定要为哪个模块写文档
- [ ] 已知道模块路径

---

## Phase 2: DEEP-READ — 深度读取模块源码

对目标模块进行彻底阅读：

```bash
# 列出模块内所有文件
find <module_path> -name "*.py" -o -name "*.ts" | sort
```

```bash
# 读取每个核心文件（排除 test、__pycache__）
# 优先读：__init__.py, registry.py, 入口文件, 数据流核心文件
```

关注点：
1. **对外接口**: 其他模块如何使用这个模块？（import 什么，调用什么函数/类）
2. **内部数据流**: 数据在模块内如何流动？
3. **关键类/函数**: 最重要的 5-10 个函数/类
4. **配置/扩展点**: 如何配置？如何扩展？
5. **依赖关系**: 依赖什么外部模块？被什么模块依赖？
6. **错误处理模式**: 异常如何处理和传播？

```bash
# 检查谁 import 了这个模块
grep -r "from <module_name>" . --include="*.py" | grep -v "__pycache__\|test_" | head -20
grep -r "import <module_name>" . --include="*.py" | grep -v "__pycache__\|test_" | head -20
```

**PHASE_2_CHECKPOINT:**
- [ ] 已读取模块内所有核心文件
- [ ] 已识别对外接口
- [ ] 已识别被哪些模块使用

---

## Phase 3: WRITE — 生成模块文档

生成 `docs/modules/<module_name>.md`，严格按以下格式：

```markdown
# [Module Name] — [一句话职责]

> **位置**: `<path>/`  
> **被谁使用**: [列出主要调用方]  
> **依赖什么**: [列出主要依赖]

## 30-Second Summary (for AI Agents)

[3-5 句话：这个模块是什么、做什么、不做什么。读完这几句话，AI Agent 应该知道是否需要深入阅读。]

**当你需要 X，看这里**: [具体文件/函数]
**当你需要 Y，看这里**: [具体文件/函数]
**当你需要 Z，不要在这里找**: [原因 + 应该去哪里]

---

## Module Structure

```
<module_path>/
├── <file1>.py          # [一句话：职责]
├── <file2>.py          # [一句话：职责]
│   └── <SubClass>      # [可选：重要的类]
└── <file3>.py          # [一句话：职责]
```

---

## Core API Reference

### `ClassName` / `function_name()`

**What it does**: [1-2句]

**Signature**:
```python
def function_name(param1: Type, param2: Type = default) -> ReturnType:
```

**Parameters**:
| 参数 | 类型 | 说明 |
|------|------|------|
| `param1` | `Type` | [说明] |

**Returns**: [返回值说明]

**Example**:
```python
# 最常见的使用方式
result = function_name(...)
```

**Pitfalls**: [常见错误/注意事项]

[重复上述格式，覆盖所有重要 API，优先级：最常用 > 最容易出错 > 扩展点]

---

## Data Flow

```
输入 ──────▶ [处理步骤 1] ──────▶ [处理步骤 2] ──────▶ 输出
                  │
                  ▼
             [副作用/存储]
```

[1-2段文字解释]

---

## Extension Guide

### 如何添加 [常见扩展场景]

1. **Step 1**: [具体操作]
2. **Step 2**: [具体操作]
3. **Checklist**:
   - [ ] [验证点]
   - [ ] [验证点]

**参考示例**: `<file>` — 完整的实现样例

---

## Common Pitfalls

| 场景 | 错误做法 | 正确做法 |
|------|---------|---------|
| [场景1] | `wrong_code()` | `correct_code()` |
| [场景2] | [描述] | [描述] |

---

## Related Docs

- [上层架构](../architecture.md) — 本模块在整体架构中的位置
- [相关模块文档](./related_module.md) — [关联原因]
```

将文档写入 `docs/modules/<module_name>.md`。

```bash
mkdir -p docs/modules
# 写入文件
```

**PHASE_3_CHECKPOINT:**
- [ ] docs/modules/<module_name>.md 已创建
- [ ] 包含 30-Second Summary
- [ ] 包含 Core API Reference（至少3个关键 API）
- [ ] 包含 Data Flow 图
- [ ] 包含 Extension Guide

---

## Success Criteria

- **DONE**: 文档已写入，AI Agent 读完 30-Second Summary 就能决定是否需要深入，读完 Core API Reference 就能直接写代码
