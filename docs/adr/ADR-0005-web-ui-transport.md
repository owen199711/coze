# ADR-0005: Web UI transport (SSE over the existing daemon)

- **状态**: Proposed
- **日期**: 2026-11-20
- **决策者**: Atlas 维护者
- **相关**: `design/atlas-ui.html`、`src/atlas/daemon.py`、`src/atlas/agent.py`、`src/atlas/tui.py`、`docs/ui-event-schema.md`、ADR-0002

## 背景

`design/atlas-ui.html` 是一个 2831 行的单文件 UI 原型：左侧会话栏、中间对话流、
右侧 Inspector（过程 / 上下文 / 知识三个 tab）、底部状态栏，外加 `/` 命令面板、
`#` 文件引用、停止按钮、Markdown 渲染、diff 视图、toast 和 modal。

它目前是**纯前端 mock**：所有数据存在 `localStorage`（key `atlas.ui.sessions.v3`），
流式效果由 `sleep()` 模拟，没有任何后端调用。

后端真实能力（`src/atlas/`）：

- `AgentRuntime.run(on_event=...)` 已有事件回调，当前发出 `model.reply`、
  `tool.call`、`tool.duplicate`、`turn.finished`；
- `SessionStore` 已有 turns / events / checkpoints / verification / usage / cost；
- `AtlasRPC.dispatch()` 已有 9 个方法（`doctor`、`session.create|get|list`、
  `chat.send`、`knowledge.search|add|validate`、`curator.run`），但类注释明确写着
  *"Methods are intentionally one-shot and synchronous. Streaming/SSE is a later
  milestone"*。

**核心矛盾**：原型要的是流式过程时间线，后端只有同步一次性 RPC。

## 决定

1. **不新建服务。** 复用现有 `daemon.py`，只增加一个 SSE 端点。按 AGENTS.md
   "不新增顶层服务/表/中间件/工具/UI 面板"，本 ADR 是新增 UI 面板的前置条件，
   而传输层本身不引入新服务。

   ```
   浏览器 (design/atlas-ui.html → src/atlas/web/)
      │  POST /rpc        (现有，同步方法，保持不变)
      │  GET  /events     (新增，SSE 流)
      ▼
   AtlasRPC.dispatch()  ──►  AgentRuntime.run(on_event=...)
                                 │
                                 └─► queue.Queue ──► SSE generator
   ```

2. **`chat.send` 增加可选 `stream` 参数。** `stream=false`（默认）保持现有同步
   行为，向后兼容；`stream=true` 时把 `on_event` 回调接到 `queue.Queue`，由
   `/events` 端点消费。**不改变现有调用方的语义。**

3. **事件 schema 先冻结再实现。** 见 `docs/ui-event-schema.md`。原型 `step(kind, ...)`
   需要的 kind（`plan` / `read` / `edit` / `check` / `answer` / `tool`）与现有
   `_emit` 事件类型之间需要一层映射，映射表写进 schema 文档。

4. **前端保持单文件，不引入构建链。** 不引入 React/Vite/npm。原型是单 HTML，
   落地时按面板拆成 3–4 个 JS 文件，CSS 不拆。理由：M0/M1 阶段依赖越少越好，
   且 `atlas` 的主入口仍是 TUI。

5. **Web UI 是可选第二客户端。** `atlas`（TUI）保持主入口不变；Web UI 通过
   `atlas serve --open` 启动。不删除 TUI。

6. **SSE 只读，不抢 session 锁。** 写操作仍走 `POST /rpc` 并复用现有
   `_session_lock`；`/events` 只消费队列，不写数据库。

## 后果

**收益**

- 零新服务、零新表，符合"一个知识服务、一个会话日志、一个执行服务"的约束。
- 现有 9 个 RPC 方法可直接支撑 Inspector 的上下文/知识 tab 和会话列表，
  这部分前端只需把 `localStorage` 换成 `fetch`。
- `on_event` 回调已存在，流式改造是接线而非重写。

**代价 / 推迟**

- 需要扩充 `_emit` 的事件类型：现有 4 种（`model.reply`/`tool.call`/`tool.result`/
  `turn.finished`）已能驱动过程面板，只需新增 `step(plan)` 和 `step(answer)` 两种。
- `turn.started` 和 `tool.duplicate` 目前只写 `sessions.event`，未 `_emit`，SSE 流里看不到。
- SSE 与同步 RPC 共享进程，长连接需要处理客户端断开和队列清理。
- `#` 文件补全没有后端索引，先用现有 `search` 工具凑合。
- 不做多用户/鉴权扩展；沿用 daemon 现有 token。
- 不做 `PLAN.md` 审批 UI 的完整流程，Plan Mode 仍以 TUI/CLI 为准。

## 落地阶段

| 阶段 | 内容 | 验收 |
|---|---|---|
| 0 | 本 ADR + `docs/ui-event-schema.md` | 文档评审通过，schema 冻结 |
| 1 | `chat.send` 支持 `stream=true`；`/events` SSE 端点；扩充 `_emit` 事件 | `curl -N` 能看到逐条事件，`turn.finished` 收尾 |
| 2 | 前端按面板接真数据（会话列表 → 对话流 → Inspector 三 tab → 设置） | 每个面板可独立验收 |
| 3 | `src/atlas/web/` 静态托管 + `atlas serve --open` | 浏览器可用，TUI 不受影响 |

## 验证

- 阶段 0：文档存在且与 `agent.py` 实际事件名一致（本 ADR 引用的 4 个事件名
  已对照 `src/atlas/agent.py` 第 245 / 278 / 359 / 424 行确认）。
- 阶段 1 起：`pytest -q` 保持全绿（当前基线 113 passed），并新增
  `tests/test_daemon.py` 的 SSE 用例。
