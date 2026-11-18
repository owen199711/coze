# UI event schema (frozen for ADR-0005)

- **状态**: Frozen (阶段 0)
- **日期**: 2026-11-20
- **相关**: `docs/adr/ADR-0005-web-ui-transport.md`、`src/atlas/agent.py`、`design/atlas-ui.html`

本文件冻结 Web UI 与 daemon 之间的事件契约。**改动本文件需要新的 ADR。**

## 1. 传输

`GET /events?session_id=<id>` 返回 `text/event-stream`。每条事件一个 SSE `data:` 帧，
内容是单行 JSON：

```
data: {"seq":1,"type":"tool.call","turn_id":"turn_ab12","ts":"2026-11-20T10:00:00Z","payload":{...}}

```

- `seq`：会话内单调递增，从 1 开始。客户端用它去重和排序。
- `type`：事件类型，见第 3 节。
- `turn_id`：所属回合。
- `ts`：ISO-8601 UTC。
- `payload`：类型相关的对象，见第 3 节。

流以 `event: end` 帧结束：

```
event: end
data: {"reason":"turn.finished"}

```

## 2. 与现有 `_emit` 的关系

`AgentRuntime.run(on_event=...)` 的回调签名是 `(event_type: str, payload: dict)`。
daemon 在 `stream=true` 时把该回调包一层，补上 `seq` / `turn_id` / `ts` 后推入
`queue.Queue`，由 `/events` 消费。

**现有 4 种事件名保持不变**（已对照 `src/atlas/agent.py` 确认）：

| 现有 `_emit` 事件 | 位置（事件名所在行） |
|---|---|
| `model.reply` | `agent.py` 第 245 行 |
| `tool.call` | `agent.py` 第 278 行 |
| `tool.result` | `agent.py` 第 359 行 |
| `turn.finished` | `agent.py` 第 424 行 |

注意：`turn.started`（第 172 行）和 `tool.duplicate`（第 345 行）目前**只写
`sessions.event`，没有 `_emit`**，因此不会出现在 SSE 流里。阶段 1 需要决定是否
补发；本 schema 暂不要求。

## 3. 事件类型与 payload

### 3.1 已有事件（不改名，只补字段）

| type | payload | 说明 |
|---|---|---|
| `model.reply` | `{step:int, content:str, tool_calls:int}` | 模型一次回复 |
| `tool.call` | `{id:str, name:str, arguments:str}` | 发起工具调用 |
| `tool.result` | `{id:str, name:str, status:str, summary:str, data_keys:list[str]}` | 工具返回，`status` 为 `ok`/`error` 等 |
| `turn.finished` | `{content:str}` | 回合结束，`content` 截断到 2000 字符 |

`tool.call` 与 `tool.result` 通过 `id` 配对，这正是原型里 step 从 `running`
变为 `done` 的信号，**阶段 1 无需新增事件即可驱动过程面板**。

### 3.2 新增事件（阶段 1 实现）

原型 `step(kind, ...)` 实际使用 6 种 kind（已用正则统计 `design/atlas-ui.html` 确认）：
`answer`(9) / `search`(8) / `read`(7) / `plan`(4) / `check`(4) / `edit`(2)。

其中 `search` / `read` / `edit` / `check` 四类**不需要新事件**：客户端把
`tool.call` 按 3.3 的映射转成 `kind`，用 `tool.result` 收尾即可。

只有两类需要新增 `step` 事件：

| type | payload | 映射来源 |
|---|---|---|
| `step` | `{kind:"plan", text:str, status:str}` | 模型输出计划 / `save_plan` |
| `step` | `{kind:"answer", text:str, status:str}` | 最终回答（与 `turn.finished` 并存） |

`status` 取值：`running` | `done` | `error` | `stopped`。

### 3.3 工具名 → kind 映射（客户端侧）

| 工具名 | kind |
|---|---|
| `search` | `search` |
| `read` | `read` |
| `edit` / `write` | `edit` |
| `check` | `check` |
| `plan` | `plan` |
| 其他 | `tool`（原型无对应图标，降级为通用步骤） |

映射在**客户端**完成，daemon 不感知 UI 的 kind 概念——这样新增工具时前端可独立适配。

## 4. 客户端约定

- 收到 `turn.finished` 后关闭 EventSource。
- `seq` 不连续时**不重连**，只记录 gap（daemon 不保证重放）。
- 未知 `type` 必须忽略，不得抛错——保证前后端可独立升级。
- 未知 `kind` 按 `tool` 渲染。

## 5. 非流式路径

`POST /rpc` 的 `chat.send` 在 `stream=false`（默认）时行为**完全不变**，
返回 `{"content": str}`。现有调用方（CLI、VSCode 扩展）不受影响。

## 6. 验证

- 阶段 1 新增 `tests/test_daemon.py::test_events_stream_emits_ordered_frames`：
  断言 `seq` 从 1 递增、以 `event: end` 收尾。
- 阶段 1 新增 `tests/test_daemon.py::test_chat_send_stream_false_is_unchanged`：
  断言默认路径返回 `{"content": ...}`。
