# ADR-0001: 回合结束自动写回候选记忆（Auto Memory Writeback）

- **状态**: Deferred (M1)
- **日期**: 2026-11-17
- **决策者**: Atlas 维护者
- **相关**: `docs/lean-atlas-v1.md` §5.2 写入策略、§6 中间件表；`docs/m0-task-board.md`

> 2026-11-18 更新：本 ADR 仍是 Proposed/Deferred，M0 未实现自动写回；当前知识写入
> 只走 `knowledge` 工具或 `atlas knowledge add`。README 的 Known limits 已同步。

## 背景

`docs/lean-atlas-v1.md` 规定记忆应当**自动沉淀**：

- §6 中间件表：`DeliveryMiddleware | on_reply | TestGate + 知识写回`，并注明
  "`Delivery` 在回复结束前跑门禁，结束后写知识"。
- §5.2 写入策略：`fact` 由 "Agent + 来源" 写、`assumption` 由 "Agent" 写，
  即回合中自动产生，而非等人手动录入。
- §5.2 统一写入管线首步为 `capture`（捕获）。

但当前 M0 实现**没有**这条自动路径：

- 仓库中不存在任何 middleware 文件（`search **/middleware*.py` → 0 结果）。
- `src/atlas/agentscope_adapter.py:11` 自述 "middleware are still pending"。
- `docs/m0-task-board.md` 将 "AgentScope tool-history/middleware" 列为**未验证**。
- 记忆只能通过三种方式写入：`knowledge` 工具显式调用、`orchestrator.remember()`
  手动调用、`curator.py` 批处理失败记录。

结果：用户观察到"记忆不会自己触发存入"，与文档意图不符。这是**功能缺口**，
不是有意的设计选择。

## 决定

在**回合结束**处增加一次自动写回，最小化落地 `DeliveryMiddleware` 的"知识写回"职责：

1. **挂载点**：`src/atlas/agent.py` 的 `AgentRuntime.run()`，在
   `turn.finished` 事件发出之后、`return final_text` 之前（当前约 288-297 行）。
   不新增中间件框架，不引入 AgentScope middleware 依赖。
2. **写入内容**：仅捕获 `fact` 与 `assumption` 两类，且 `status="candidate"`。
   不自动写 `preference`（需用户确认）、`decision`（需人审）、`lesson`（归 Curator）。
3. **写入通道**：复用现有 `KnowledgeService.write(item, actor)`，走既有
   `capture → normalize → dedupe → classify → validate → status → chunk/index → audit`
   管线，不新增服务、不新增表。
4. **来源可追溯**：每条自动写入必须带 `source`（session_id + turn_id），
   满足 §5.2 "来源可追溯" 校验。
5. **可关闭**：通过配置开关（默认开启）允许用户禁用自动写回。

## 后果

**收益**

- 记忆自动沉淀，符合 `lean-atlas-v1.md` §5.2/§6 的既定意图。
- 复用 `KnowledgeService`，不破坏"一个知识服务"的耐久核心约束。

**代价 / 推迟**

- 按 AGENTS.md "加一个能力、移除或推迟一个旧东西"：**推迟
  `KnowledgeRecallMiddleware`（自动召回）**。召回在本次变更后仍由
  `build_system_prompt` 的 top-12 注入 + `knowledge` 工具手动触发承担。
- 自动写回会增加每回合一次 LLM 抽取调用（或规则抽取）的 token 成本，
  需纳入 `ContextMiddleware` 预算讨论（后续 ADR）。

**风险**

- 候选记忆可能污染知识库。缓解：`status="candidate"` 默认不进入高置信召回，
  依赖既有 `promote` 流程晋升；`dedupe` 步骤防止重复。
- 抽取质量依赖模型。缓解：先写失败测试锁定行为，再实现；抽取失败时静默跳过，
  不得让写回失败影响回合返回。

## 备选方案

| 方案 | 结论 |
|---|---|
| A. 回合结束自动写回候选（本 ADR） | **采纳** |
| B. 仅更新文档，标注 middleware 推迟到 M1 | 不采纳：用户明确期望自动记忆 |
| C. 完整实现 AgentScope 8 中间件框架 | 不采纳：超出 M0 范围，违反"耐久核心要小" |
| D. 保持纯手动写入 | 不采纳：与文档 §5.2/§6 冲突 |

## 验证计划

1. 先写失败测试：一次 `AgentRuntime.run()` 后，`knowledge.list()` 中出现
   带 `source` 的 `candidate` 条目。
2. 实现后 `pytest -q` 全绿。
3. 手动 `atlas chat` 一轮，确认 `knowledge` 面板可见新候选。
