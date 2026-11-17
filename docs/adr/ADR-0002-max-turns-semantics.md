# ADR-0002: `max_turns` 语义对齐 openai-agents（一次 LLM 调用 = 一个 turn，超限抛异常）

- **状态**: Accepted
- **日期**: 2026-11-18
- **决策者**: Atlas 维护者
- **相关**: `src/atlas/agent.py` `AgentRuntime.run()`、`src/atlas/config.py`、`src/atlas/llm.py`、`tests/test_agent.py`

## 背景

`max_turns` 目前是 Atlas 自有配置（`config.py:40`，默认 `12`；`atlas.yaml:32` 设为 `24`），
与 `openai-agents` 库无关——本仓库的 agent 依赖是 `agentscope`，不是 `openai-agents`。
但两者字段同名，语义却不同，容易误读。

当前实现（`agent.py` `run()`）的语义是：

1. **turn = 一轮连续工具调用**，不是一次 LLM 调用（`agent.py:154` 注释明说）。
2. `max_turns` 是**软预算**：`hard_cap = max(limit, max_tool_calls)`，循环跑 `hard_cap` 次。
3. **可续期**：某轮 `round_progressed` 且 `tool_calls_used < hard_cap` 时，
   `limit = min(limit + soft_span, hard_cap)`，并记 `turn.budget_extended` 事件。
4. **超限不报错**：`exhausted = True` 后调 `_finalize()`，用已有证据合成答案返回。

`openai-agents` 的语义是：

1. **turn = 一次 LLM 调用**。
2. 默认 `max_turns = 10`。
3. 超限**抛 `MaxTurnsExceeded`**，不静默收敛。

用户要求对齐 `openai-agents` 语义。这是一次**行为变更**，按 `AGENTS.md`
（"Do not add a new top-level service, table, middleware, tool, or UI panel without an ADR"）
与 `docs/m0-task-board.md`（"one task = one verifiable change"）需要 ADR。

## 决定

1. **turn 定义**：一次 LLM 调用（一次 `model.chat` / `model.chat_stream`）计为一个 turn。
   工具调用不再单独消耗 turn 预算。
2. **默认值**：`AtlasConfig.max_turns` 默认 `12` → `10`，对齐 `openai-agents`。
3. **超限行为**：抛 `MaxTurnsExceeded`（新增于 `src/atlas/llm.py`，继承 `ModelError`），
   携带 `max_turns` 与已产生的 `messages`，供调用方决定是否续跑。
4. **移除软预算续期**：删除 `soft_span` / `limit` 续期分支与 `turn.budget_extended` 事件。
   `max_tool_calls` 保留为**独立的**硬上限（防止单轮内工具风暴），语义不变。
5. **保留 `_finalize()`**：仅用于"模型返回空内容"的兜底，不再用于预算耗尽。

## 后果

**收益**

- `max_turns` 语义与业界主流（`openai-agents`）一致，降低误读成本。
- 预算耗尽变成**显式失败**，不再静默返回一个可能不完整的答案。

**代价 / 推迟**

- 按 `AGENTS.md` "加一个能力、移除或推迟一个旧东西"：**移除软预算续期机制**
  （`turn.budget_extended` 事件随之消失）。长任务需要调用方捕获 `MaxTurnsExceeded`
  后显式续跑，而不是靠自动续期。
- **破坏性变更**：`tests/test_agent.py::test_agent_finalizes_after_tool_budget`
  断言 `"I stopped after" not in reply` 且 `model.final_calls == 1`，该测试必须重写。
- `atlas.yaml:32` 的 `max_turns: 24` 需同步评估（24 次 LLM 调用上限是否仍合适）。

**风险**

- 现有用户依赖"长任务自动续期"，升级后会遇到 `MaxTurnsExceeded`。
  缓解：异常携带完整 `messages`，调用方可无损续跑；在 README 标注为破坏性变更。
- `max_tool_calls` 与 `max_turns` 语义分离后，两者关系需在文档中写清。

## 备选方案

| 方案 | 结论 |
|---|---|
| A. 对齐 openai-agents：turn=LLM 调用，超限抛异常（本 ADR） | **采纳** |
| B. 仅把 `atlas.yaml` 的 `24` 改成 `10`，语义不变 | 不采纳：只改数字，语义仍不一致 |
| C. 保留软预算，仅新增 `MaxTurnsExceeded` 供调用方选择 | 不采纳：两套语义并存更易误读 |
| D. 维持现状，只补文档说明 | 不采纳：用户明确要求对齐 |

## 验证计划

1. 先写失败测试（`tests/test_agent.py`）：
   - `test_max_turns_counts_llm_calls`：模型每步都调工具，`max_turns=3` 时
     `model.calls == 3` 后抛 `MaxTurnsExceeded`。
   - `test_max_turns_exceeded_carries_messages`：异常携带 `max_turns` 与 `messages`。
   - `test_max_turns_default_is_ten`：`AtlasConfig(root=...)` 的 `max_turns == 10`。
   - 重写 `test_agent_finalizes_after_tool_budget` 为 `test_max_turns_exceeded_raises`。
2. 实现后 `pytest -q` 全绿。
3. 手动 `atlas chat` 一轮，确认超限时 CLI 给出可读错误而非崩溃。
