# Lean Atlas v1：8 周可用的 Coding Agent 实现清单

> 状态：**v1 构建规格（Build Spec）** · 日期：2026-11-17
> 定位：不是“完整设计空间”，而是**你要真正写代码时照着做的清单**。
> 注意：这是 **8 周 MVP**，不是“完全成熟落地”的终局；从团队可用到生产托管的完整路线见 [Atlas 成熟路线图](./atlas-maturity-roadmap.md)。
> Python M0 代码：[README](../README.md) · [src/atlas](../src/atlas)
> 上游：[Atlas 完整技术方案](./atlas-coding-agent-design.md) · [架构评审](./atlas-architecture-review.md)
> 一句话：**一个知识服务、一个会话日志、一个执行服务；4+1 个 UI，12 个工具，8 个中间件。**

---

## 0. 先看这一页

### 0.1 目标

做一个**单仓库、单用户、本地优先**的 Coding Agent：

- 能在真实仓库里读代码、改代码、跑测试、开草案 PR；
- 有 Plan Mode、测试先行、证据门禁；
- 能跨会话记住项目事实、用户偏好、经验教训；
- 有一个“留守线程”在空闲时整理知识、提炼经验；
- VSCode 里 4 个主面板就能完成 90% 工作；
- 8 周内可用，而不是 6 个月后“什么都有”。

### 0.2 非目标（v1 明确不做）

- 不做多租户/团队服务端；
- 不做多仓库/多服务编排；
- 不做模型权重训练；
- 不做 L3–L6 自我演进（不改核心代码、不做 EvolutionSandbox）；
- 不做 GraphRAG/组织知识库；
- 不做完整记忆浏览器/知识库浏览器/风格向导；
- 不做浏览器自动化、K8s、Jira/Confluence 集成；
- 不做“零错误”承诺。

### 0.3 一句话架构

```
VSCode（Chat / Plan / Diff / Tasks / Review）
        │ JSON-RPC
Orchestrator（会话、审批、任务、预算）
        │ reply_stream
AgentRuntime（AgentScope Agent + 8 中间件）
        │ tools
KnowledgeService     CodebaseService     ExecutionService
（一个知识库）        （项目/模块/合同）    （工具/沙箱/测试）
        │
Platform（SQLite + 模型 + 事件 + 遥测 + 协议）
```

### 0.4 数量约束

| 维度 | v1 上限 |
|---|---|
| 必选服务 | 6 |
| 可选服务 | 1（Eval，先脚本后服务） |
| 中间件 | 8 |
| 工具 | 12 |
| 数据表 | 15（含可选） |
| 配置 | 2 必需 + 1 可选 |
| UI 主面板 | 4 + 1（Review） |
| 知识层 | 3（working/project/user-org） |
| 存储 | 2（事件日志 + 知识库） |

---

## 1. 范围：In / Out

### 1.1 v1 必须做（P0）

| 能力 | 验收 |
|---|---|
| 会话与恢复 | 杀进程后能继续；检查点可回放 |
| Agent 运行时 | AgentScope `Agent` + 8 中间件跑通 |
| 12 个工具 | 读/改/搜/执行/git/检查/LSP/web/提问/知识/计划/子 Agent |
| Plan Mode | 只读探索 → `PLAN.md` → 人工批准 → 执行 |
| 测试先行 | 红-绿证据；TestGate 阻断未验证交付 |
| 知识库 | 事实/偏好/经验/文档/命令统一存储；可检索/可删除 |
| 留守线程 | 空闲时整理知识、验证经验、生成报告 |
| 项目契约 | `atlas.yaml` + `AGENTS.md`；模块/命令/风格 |
| VSCode | Chat/Plan/Diff/Tasks；Review 收件箱 P1 |
| 评测 | 10 个 golden tasks + 重复错误基线 |

### 1.2 v1 可以后置（P1/P2）

- P1：子 Agent、合同检查、知识卡片、Review 收件箱、上下文检查器；
- P2：多仓库、团队服务端、真实浏览器、风格向导、组织知识；
- 永不做（v1）：核心代码自修改、权重训练、无边界自治。

---

## 2. 架构：6 个必选 + 1 个可选服务

| 服务 | 职责 | 不做什么 |
|---|---|---|
| **Orchestrator** | 会话、轮次、审批、任务 DAG、预算、调度 | 不组装 prompt，不直接调模型 |
| **AgentRuntime** | AgentScope `Agent`、8 个中间件、prompts、状态 | 不直接写数据库，不直接跑 shell |
| **KnowledgeService** | 知识写入/检索/生命周期/Curator | 不做代码索引，不做执行 |
| **CodebaseService** | `atlas.yaml`、模块图、合同、LSP/tree-sitter、影响分析 | 不做知识策展 |
| **ExecutionService** | 工具、沙箱、测试/lint/build、证据 | 不做计划/审批 |
| **Platform** | SQLite/Postgres、模型网关、事件总线、OTel、JSON-RPC | 不含业务规则 |
| EvalService（可选） | 离线评测、回放、指标 | 不参与线上请求 |

### 2.1 边界规则

| 边界 | 契约 |
|---|---|
| UI ↔ Orchestrator | JSON-RPC 2.0；事件流 |
| Orchestrator ↔ AgentRuntime | `AgentState` + `reply_stream` |
| AgentRuntime ↔ Knowledge | `knowledge` 工具 + `KnowledgeRecall`/`Delivery` 中间件 |
| AgentRuntime ↔ Codebase | `ProjectContract` 中间件 + `CodebaseService` 查询 |
| AgentRuntime ↔ Execution | 12 个工具；所有命令走沙箱 |
| Platform ↔ 所有 | 仓库/模型/事件/遥测接口 |

**硬规则**：任何服务不得直接访问另一个服务的表；只能通过接口。

---

## 3. 代码结构

```
atlas/
├── pyproject.toml
├── src/atlas/
│   ├── daemon/
│   │   ├── server.py            # JSON-RPC / WebSocket
│   │   ├── protocol.py          # request/response/event types
│   │   └── session_actor.py     # 单会话串行队列
│   ├── orchestrator/
│   │   ├── sessions.py
│   │   ├── approvals.py
│   │   ├── tasks.py             # plan + DAG
│   │   └── budgets.py
│   ├── agent/
│   │   ├── factory.py           # build_agent()
│   │   ├── prompts/
│   │   └── middleware/
│   │       ├── telemetry.py
│   │       ├── context.py
│   │       ├── journal.py
│   │       ├── inbox.py
│   │       ├── knowledge_recall.py
│   │       ├── project_contract.py
│   │       ├── policy.py
│   │       └── delivery.py
│   ├── knowledge/
│   │   ├── models.py            # KnowledgeItem
│   │   ├── service.py           # search/get/write/feedback/forget
│   │   ├── writer.py            # 按 kind 的 extractor
│   │   ├── retriever.py         # FTS + 可选向量
│   │   ├── curator.py           # 留守 Job
│   │   └── index.py
│   ├── codebase/
│   │   ├── config.py            # atlas.yaml
│   │   ├── graph.py             # tree-sitter 模块图
│   │   ├── lsp.py
│   │   └── contracts.py
│   ├── execution/
│   │   ├── sandbox.py
│   │   ├── tools/               # 12 个 ToolBase
│   │   └── verify.py            # 测试/lint/证据
│   ├── platform/
│   │   ├── storage.py           # SQLite/Postgres
│   │   ├── models.py
│   │   ├── models_gateway.py
│   │   ├── events.py
│   │   └── telemetry.py
│   └── eval/
├── extension/                   # VSCode TypeScript
├── schemas/                     # JSON Schema
└── tests/
```

---

## 4. 数据模型：15 张表

### 4.1 核心表

| 表 | 用途 |
|---|---|
| `projects` | 仓库/项目身份 |
| `sessions` | 会话 |
| `turns` | 轮次 |
| `events` | 事件流（含 `knowledge.signal`） |
| `checkpoints` | `AgentState` + 工作区快照 |
| `plans` | 计划（含 unknowns/assumptions JSON） |
| `tasks` | 任务 DAG |
| `verifications` | 测试/检查证据（不可变） |
| `approvals` | 审批 |
| `jobs` | 后台任务/Curator |
| `usage` | token/费用 |
| `artifacts` | 大文件引用 |
| `knowledge_items` | **所有知识** |
| `knowledge_chunks` | 检索分块 |
| `knowledge_links` | 可选关系（v1 可不建） |

### 4.2 `knowledge_items`

```sql
CREATE TABLE knowledge_items (
  id              TEXT PRIMARY KEY,
  tenant_id       TEXT NOT NULL,
  project_id      TEXT,
  owner_id        TEXT,
  scope           TEXT NOT NULL,       -- user | project | org
  kind            TEXT NOT NULL,       -- preference|fact|decision|lesson|doc|command|contract|assumption|glossary
  status          TEXT NOT NULL,       -- candidate|active|stale|retired|quarantined
  title           TEXT NOT NULL,
  body            TEXT NOT NULL,
  tags_json       TEXT,
  source_json     TEXT NOT NULL,       -- session/file/url/command + hash/time
  confidence      REAL DEFAULT 0.8,
  severity        TEXT,                -- low|medium|high
  validation_json TEXT,                -- lesson: replay/test/human
  freshness_at    TIMESTAMP,
  expires_at      TIMESTAMP,
  supersedes_json TEXT,
  sensitivity     TEXT DEFAULT 'internal',
  created_at      TIMESTAMP NOT NULL,
  updated_at      TIMESTAMP NOT NULL
);

CREATE INDEX idx_knowledge_scope ON knowledge_items(tenant_id, project_id, scope, status);
CREATE INDEX idx_knowledge_kind ON knowledge_items(project_id, kind, status);

CREATE TABLE knowledge_chunks (
  id            TEXT PRIMARY KEY,
  item_id       TEXT NOT NULL REFERENCES knowledge_items(id) ON DELETE CASCADE,
  project_id    TEXT,
  ordinal       INTEGER,
  content       TEXT NOT NULL,
  symbol        TEXT,
  start_line    INTEGER,
  end_line      INTEGER,
  token_count   INTEGER,
  embedding_ref TEXT
);
```

### 4.3 表意规则

- **一个知识表**覆盖 memory + lessons + PKB；
- `kind` 决定写入校验/召回时机/TTL；
- `scope` 决定隔离与权限；
- `status` 决定生命周期；
- **不建** `memory_items`、`lessons`、`learning_*`、`kb_*`、`unknowns`、`assumptions`；
- `unknowns/assumptions` 放 `plans.payload_json`；需要长期跟踪时用 `kind=assumption`；
- 证据放 `verifications`，不放进知识库；
- 代码索引不建表，放 `CodebaseService` 的缓存/索引目录。

---

## 5. 知识模型

### 5.1 一个 `KnowledgeItem`

```python
class KnowledgeItem(BaseModel):
    id: str
    tenant_id: str
    project_id: str | None
    owner_id: str | None = None
    scope: Literal["user", "project", "org"]
    kind: Literal[
        "preference", "fact", "decision", "lesson",
        "doc", "command", "contract", "assumption", "glossary",
    ]
    status: Literal["candidate", "active", "stale", "retired", "quarantined"]
    title: str
    body: str
    tags: list[str] = []
    source: SourceRef
    confidence: float = 0.8
    severity: Literal["low", "medium", "high"] = "medium"
    validation: Validation | None = None
    freshness_at: datetime
    expires_at: datetime | None = None
    supersedes: list[str] = []
    sensitivity: Literal["public", "internal", "secret"] = "internal"
```

### 5.2 写入策略

| kind | 谁写 | 校验 | 初始状态 |
|---|---|---|---|
| preference | 用户/Agent（确认） | 用户确认 | active |
| fact | Agent + 来源 | 来源可追溯 | active（低置信 candidate） |
| decision | 计划批准 | 人审 | active |
| lesson | Curator | 回放/测试/人审 | candidate → active |
| doc | Curator/人 | 来源 hash + 关键卡人审 | active |
| command | 验证通过 | 命令证据 | active |
| contract | 合同变更 | 合同校验 | active |
| assumption | Agent | 依据 + 过期 | candidate/active |
| glossary | 人/Agent | 来源 | active |

**统一写入管线**：

```
capture → normalize → dedupe → classify(kind) → validate → status → chunk/index → audit
```

### 5.3 召回策略

| kind | 时机 | 预算 |
|---|---|---|
| preference | 每轮常驻核心 | ≤ 300 tokens |
| fact/decision | 计划/探索 | ≤ 800 tokens |
| lesson | 计划开始、工具前、评审 | ≤ 800 tokens |
| doc | 探索/按需 | ≤ 3000 tokens |
| command | 工具前 | ≤ 300 tokens |
| contract | 计划/影响 | ≤ 500 tokens |
| assumption | 计划/交付报告 | ≤ 300 tokens |

**一条检索管线**：

```
query → scope/kind filter → FTS5/BM25 (+ optional vector)
      → dedupe → rank(relevance×confidence×freshness×severity)
      → budget → summary + citation
```

### 5.4 统一 API

```python
knowledge.search(query, scope=None, kinds=None, limit=8, budget_tokens=2000)
knowledge.get(id)
knowledge.write(item, actor)
knowledge.feedback(id, outcome)     # effective/ineffective
knowledge.forget(id, actor)
knowledge.refresh(project_id)       # 手动触发 Curator
```

### 5.5 生命周期

```
candidate → active → stale → retired
                 └→ quarantined
```

| kind | TTL/失效 |
|---|---|
| preference | 长期；用户可改 |
| fact | 来源变 → stale |
| decision | 长期 |
| lesson | 90 天未命中降级；3 次命中仍复发淘汰 |
| doc | 来源 hash 变 → stale |
| command | 版本变 → stale |
| contract | 合同版本变 → stale |
| assumption | 到期 retired |

### 5.6 CuratorJob（留守线程）

**不是服务**，是 `KnowledgeService` 的后台 Job：

```
触发：用户空闲 15min / 夜间 / 事件 / 手动
队列：P0 纠正/安全 → P1 测试失败/评审/回滚 → P2 CI/重试 → P3 文档/知识库
资源：单飞、低优先级、预算上限、前台活跃暂停、低电量暂停
产物：knowledge_items / reports / 候选 lesson
```

夜间循环：

```python
class CuratorJob:
    async def run_once(self, project_id: str):
        if not self.should_run(project_id):
            return
        async with self.single_flight(project_id), self.budget.window("curator"):
            await self.knowledge.consolidate(project_id)      # 去重/合并/过期
            for signal in await self.knowledge.claim_signals(project_id, limit=50):
                lesson = await self.analyze(signal)           # RCA
                if lesson and await self.validate(lesson):    # replay/test
                    await self.knowledge.promote(lesson)
            await self.knowledge.refresh_index(project_id)     # 增量
            await self.report(project_id)
```

**v1 不做**：改核心代码、自动改规则、自动改 prompt；只写知识/候选/报告。

---

## 6. Agent 组装：8 个中间件

```python
async def build_agent(session, deps) -> Agent:
    state = await deps.sessions.load_state(session.id) or AgentState(session_id=session.id)

    toolkit = Toolkit(tools=[
        Read(), Edit(), Search(), Shell(), Git(), Check(),
        Lsp(), Web(), AskUser(), Knowledge(), Plan(), Subagent(),
    ])

    middlewares = [
        TelemetryMiddleware(deps.events),
        ContextMiddleware(deps.context),          # budget + compaction + manifest
        JournalMiddleware(deps.checkpoints),
        InboxMiddleware(deps.inbox),
        KnowledgeRecallMiddleware(deps.knowledge),
        ProjectContractMiddleware(deps.codebase),
        PolicyMiddleware(deps.policy),            # plan + tool + uncertainty
        DeliveryMiddleware(deps.gate, deps.knowledge),
    ]

    return Agent(
        name="atlas",
        system_prompt=build_system_prompt(session, deps),
        model=deps.models.for_role("coder"),
        toolkit=toolkit,
        state=state,
        middlewares=middlewares,
        context_config=ContextConfig(
            trigger_ratio=0.80,
            reserve_ratio=0.22,
            tool_result_limit=6000,
            compression_tool_enabled=True,
        ),
        injection_config=InjectionConfig(timezone="Asia/Shanghai"),
    )
```

### 6.1 中间件职责

| 中间件 | 钩子 | 职责 |
|---|---|---|
| `TelemetryMiddleware` | `on_reply`, `on_model_call`, `on_acting` | 事件、trace、成本、脱敏 |
| `ContextMiddleware` | `on_compress_context`, `on_reasoning` | 预算、压缩、manifest |
| `JournalMiddleware` | `on_reply` | 检查点、恢复 |
| `InboxMiddleware` | `on_reasoning` | 插话、后台结果、审批结果 |
| `KnowledgeRecallMiddleware` | `on_reasoning` | top-k 知识注入 |
| `ProjectContractMiddleware` | `on_system_prompt` | 项目/风格/模块摘要 |
| `PolicyMiddleware` | `on_reasoning`, `on_acting` | Plan 只读、工具规则、未知提问 |
| `DeliveryMiddleware` | `on_reply` | TestGate + 知识写回 |

**规则**：

- `Policy` 内部可拆三个模块，但对外一个中间件；
- `Delivery` 在回复结束前跑门禁，结束后写知识；
- 不要再用 `MemoryRecall`/`LessonRecall`/`MemoryWriteback`/`LearningCapture` 四套。

**边界说明**：

- **预算分两层**：Orchestrator 管 session/task 硬预算与暂停；`ContextMiddleware` 管单次请求的装配、软压缩与 manifest。
- **检查点分工**：`JournalMiddleware` 只负责“何时写”；Orchestrator 负责“加载/恢复/租约/清理”，二者通过 `CheckpointStore` 接口交互。
- **材料分工**：Knowledge/Codebase 只提供材料；`ContextMiddleware` 负责装配，不反向调用业务服务。

---

## 7. 工具：12 个

| 工具 | 动作 | 关键约束 |
|---|---|---|
| `read` | 读文件/行范围 | 路径必须在工作区 |
| `edit` | create/replace/patch | 修改前必须读过；diff 上下文匹配 |
| `search` | files/content | 用 ripgrep/glob；结果上限 |
| `shell` | 执行命令 | 沙箱；命令规则；超时 |
| `git` | status/diff/log/branch/commit | 不 force push；不改 main |
| `check` | test/lint/typecheck/validate | 返回结构化失败；写证据 |
| `lsp` | diagnostics/symbols/references/rename | rename 用 WorkspaceEdit |
| `web` | search/fetch/docs | 来源引用；网页不可信 |
| `ask_user` | 2–4 选项 + 推荐 + 影响 | 单轮最多 2 问 |
| `knowledge` | search/get/write/feedback | 写入需权限；kind 策略 |
| `plan` | write/exit/tasks | 只读阶段唯一写入口 |
| `subagent` | spawn/send/list/output | 默认同步；后台结果回注 |

**合并规则**：

- `write_file` + `edit_file` + `apply_patch` → `edit`；
- `glob` + `grep` → `search`；
- `run_shell` + `env_probe` → `shell`；
- `run_tests` + lint + typecheck + project_validate → `check`；
- `web_search` + `web_fetch` + `docs_lookup` → `web`；
- `memory_*` + `lesson_*` + `kb_*` → `knowledge`；
- `plan_write` + `plan_exit` + `task_*` → `plan`。

---

## 8. 配置：2+1 份

### 8.1 `atlas.yaml`

```yaml
version: 1
project:
  name: acme
  root: .
  languages: [python, typescript]
  commands:
    setup: pnpm install
    test: pnpm -w test
    lint: pnpm -w lint
    typecheck: pnpm -w typecheck
    format: pnpm -w format
  modules:
    - id: api
      path: services/api
    - id: web
      path: apps/web

style:
  profile: pep8-ruff        # 预设；自定义才展开
learning:
  enabled: true
  idle_minutes: 15
  budget:
    max_cost_usd_per_night: 2.0
permissions:
  mode: accept_edits        # default | accept_edits | explore | dont_ask
```

### 8.2 `AGENTS.md`

```markdown
# AGENTS.md
## 命令
- 测试：pnpm -w test
- 后端：cd services/api && pytest -q
## 规则
- 不直接改 main；不 force push；不提交 .env
- 前端不 import 后端源码；用 contracts/ 生成 client
## 完成定义
- 测试绿、lint/typecheck 绿、结构规则零新增、PR 有证据
```

### 8.3 可选 `permissions.yaml`

仅企业/安全覆盖需要；个人用户不需要。

---

## 9. 协议：JSON-RPC + 事件

### 9.1 方法

```
session.create / session.resume / session.close / session.list
chat.send / chat.interrupt
approval.respond
plan.get / plan.approve / plan.reject
diff.apply / diff.revert
task.list / task.retry
knowledge.search / knowledge.get / knowledge.delete
review.list / review.approve / review.reject
```

### 9.2 事件

```
turn.started / message.delta / tool.call / tool.result
approval.request / approval.resolved
plan.updated / task.updated / context.manifest
knowledge.updated / verification.updated
turn.finished / error
```

事件统一字段：`seq/session_id/turn_id/timestamp/type/payload`。

---

## 10. 关键流程

### 10.1 一次任务

```
用户消息
 → Orchestrator 创建 turn，加载 checkpoint
 → AgentRuntime 组装 Agent
 → Policy 判断：复杂任务？→ Plan Mode
 → 只读探索 → plan.write → plan.exit（人审）
 → 任务 DAG（plan.tasks）
 → 每个任务：写失败测试 → 实现 → 检查 → 证据
 → Delivery：TestGate 通过？不通过再跑；通过写知识
 → Diff/草案 PR
 → Curator 入队（空闲时复盘）
```

### 10.2 知识写入

```
用户纠正 / 测试失败 / 评审 finding / 回滚
 → LearningSignal（events 表）
 → CuratorJob 认领
 → RCA → Lesson（candidate）
 → 回放/测试/人审
 → active（或 quarantined）
 → 索引 → 下次 KnowledgeRecall 可用
```

### 10.3 恢复

```
load checkpoint → 校验 git SHA/工作区 → 对账未完成 tool.intent
 → 恢复 AgentState → 继续
```

---

## 11. 权限与安全（v1）

- 模式：`default` / `accept_edits` / `explore` / `dont_ask`；
- 默认 `accept_edits`：工作区内自动，越界 ASK；
- 危险命令/路径/密钥：deny 规则；
- 沙箱：本地受限模式优先，Docker 可选；
- 密钥：VSCode `SecretStorage`，不入日志/知识；
- 网页/Issue/代码注释：标记 untrusted，不作为指令；
- 写入知识前：敏感信息扫描；
- v1 不做自修改权限/安全策略。

---

## 12. 评测（v1）

### 12.1 任务集

- 10 个 golden tasks/仓库：bug、特性、重构、测试、边界；
- 每个任务有隐藏测试/验收脚本；
- 记录基线：无知识/无经验。

### 12.2 指标

| 指标 | 目标 |
|---|---|
| 任务通过率 | ≥ 60% |
| 测试通过率 | ≥ 95% |
| 人工接受率 | ≥ 50% |
| 重复错误率 | 相对基线下降 ≥ 20%（8 周） |
| 单任务成本 | ≤ $2 |
| 恢复成功率 | 100% |
| 新工程师跑通主路径 | ≤ 1 天 |

### 12.3 命令

```bash
atlas eval run --suite repo:acme --tasks 10 --repeat 3
atlas eval learning --baseline no-knowledge --inject active
```

---

## 13. 8 周里程碑

| 周 | 交付 | 验收 |
|---|---|---|
| **W1** | 仓库骨架、`atlas.yaml`、SQLite schema、sessions/turns/events/checkpoints、JSON-RPC daemon | 能创建会话、发消息、落库、恢复 |
| **W2** | AgentRuntime + 8 中间件骨架；read/edit/search/shell/git | 单文件任务端到端 |
| **W3** | check/test、verification、Plan Mode、plan/task DAG | 计划 → 红-绿 → 证据 |
| **W4** | KnowledgeService、knowledge 工具、lesson 生命周期、FTS5 | 跨会话召回；知识可删除 |
| **W5** | CodebaseService、`atlas.yaml` 解析、模块图、影响分析 | 跨模块任务 |
| **W6** | VSCode Chat/Plan/Diff/Tasks + Review（P1） | 插件内完成 5 个任务 |
| **W7** | CuratorJob、知识巩固、周报、指标 | 重复错误下降 ≥ 20% |
| **W8** | 10 个 golden tasks、安全/恢复/成本硬化、试点 | 验收指标达标 |

### 13.1 每周节奏

- 周一：定本周 1 个用户可见目标；
- 周三：端到端 demo（不是单元测试）；
- 周五：指标 + 删除清单（本周新增了什么，能否删掉一个旧东西）；
- 每两周：一次“复杂度预算”评审。

---

## 14. 验收清单

- [ ] 单仓库、单用户、单语言跑通；
- [ ] 5 个真实任务端到端：计划 → 红-绿 → 证据 → 草案 PR；
- [ ] `kill -9` 后恢复；
- [ ] 知识写入/检索/删除/过期；
- [ ] 经验候选 → 验证 → 召回 → 复发度量；
- [ ] Curator 不影响前台 P95 首 token；
- [ ] VSCode 4 个主面板可用；
- [ ] 成本 ≤ $2/简单任务；
- [ ] 新工程师 1 天跑通主路径；
- [ ] 无 P0 安全事件。

---

## 15. 明确后置

| 功能 | 后置到 | 条件 |
|---|---|---|
| 多仓库/多服务 | v1.5 | 首个客户确实跨仓库 |
| 团队服务端/多租户 | v2 | 本地版验证价值后 |
| 自我演进 L3–L6 | v2 | 经验闭环稳定、评测可信 |
| GraphRAG | v1.5 | FTS+向量不够用 |
| 风格向导 | v1.5 | 用户不愿手写 style |
| 完整记忆/知识库浏览器 | v1.5 | Review 收件箱不够用 |
| 浏览器自动化 | v2 | 有明确 UI 任务需求 |
| 组织知识库 | v2 | 有团队共享需求 |

**后置规则**：没有真实用户案例，不做。

---

## 16. 复杂度预算

| 维度 | 上限 | 超出时 |
|---|---|---|
| 必选服务 | 6 | 删除/合并 ≥2 个，或 ADR |
| 中间件 | 8 | 证明现有钩子覆盖不了 |
| 工具 | 12 | 证明核心工具不能加 `mode` |
| 表 | 15 | 证明不能表达为 `kind`/JSON |
| 配置 | 2+1 | 必须零配置默认 |
| UI 主面板 | 4+1 | 替换旧面板或 feature flag |

**每个 PR 问 6 个问题**：

1. 能否作为现有 `kind` 的值？
2. 能否作为现有服务的模块？
3. 能否作为现有中间件的一条规则？
4. 能否作为现有工具的一个 `mode`？
5. 能否默认关闭/零配置？
6. 删掉它，用户会失去什么？

---

## 17. 与完整文档的映射

| 完整文档 | v1 处置 |
|---|---|
| 第 6 章 记忆 L0–L4 | **合并**进 KnowledgeService；只留 3 层 |
| 第 22 章 学习/知识库 | **合并**；只留 L1/L2；Curator 变 Job |
| 第 7 章 Session Journal | 保留，改名“会话日志” |
| 第 8 章 项目契约 | 合并进 CodebaseService |
| 第 9 章 多项目 | 后置 |
| 第 10 章 规划/测试 | 保留 |
| 第 11 章 不确定性 | 合并进 Policy |
| 第 12 章 长任务 | 保留（Orchestrator/Journal） |
| 第 13 章 上下文 | 保留（ContextMiddleware） |
| 第 14 章 VSCode | 裁剪到 4+1 |
| 第 15 章 工具 | 裁剪到 12 |
| 第 16 章 安全 | 保留 |
| 第 17 章 数据模型 | 裁剪到 15 表 |
| 第 18 章 评估 | 保留，缩小 |
| 第 20 章 路线图 | 用本文 8 周计划 |
| 第 22 章 六级演进 | v1 只做 L1/L2 |

**v1 不创建的类/表/中间件**：

- 类：`MemoryService`、`LearningService`、`ProjectKnowledgeBase`、`LessonStore`、`EvolutionSandbox`；
- 表：`memory_items`、`lessons`、`learning_signals`、`learning_candidates`、`learning_runs`、`kb_documents`、`kb_chunks`、`kb_entities`、`unknowns`、`assumptions`；
- 中间件：`MemoryRecall`、`LessonRecall`、`MemoryWriteback`、`LearningCapture`、`Budget`、`Compaction`、`TestGate`（合并为 8 个）；
- UI：记忆浏览器、知识库浏览器、风格向导、上下文检查器、证据面板、检查点时间线、多仓库视图。

> 这些能力不是被否定，而是被合并/后置；v1 的代码里不应出现这些名字。

---

## 18. 第一周任务清单（可直接开工）

### Day 1

- 建 `pyproject.toml`，依赖：`agentscope`、`pydantic`、`fastapi`/`uvicorn`（或纯 JSON-RPC）、`aiosqlite`、`tree-sitter`、`ruff`、`pytest`；
- 建 `src/atlas/platform/models.py`：15 张表；
- 建 `atlas.yaml` + `AGENTS.md` 模板。

### Day 2

- `daemon/server.py`：JSON-RPC over stdio/WebSocket；
- `session.create` / `chat.send` / `turn.finished`；
- `SessionActor` 单会话队列。

### Day 3

- `AgentRuntime.build_agent()`：AgentScope `Agent` + 空中间件；
- `read/edit/search/shell` 4 个工具；
- 单文件任务 demo。

### Day 4

- `JournalMiddleware` + checkpoint；
- `kill -9` 恢复测试；
- `events` 落库。

### Day 5

- `git/check` 工具；
- `verifications` 表 + 证据；
- 第一次端到端 demo（读 → 改 → 测试 → 证据）。

### Day 6–7

- 补齐 8 个中间件骨架（空实现也可）；
- 写 3 个 golden tasks；
- 周复盘：删掉一个不必要的类/表/配置。

---

## 19. 风险与对策

| 风险 | 对策 |
|---|---|
| AgentScope API 变动 | pin minor；适配层；契约测试 |
| VSCode API 变动 | Webview + JSON-RPC；不用 Chat Participant |
| 知识库变垃圾场 | kind 配额；TTL；审核；删除 |
| 学习剧场 | 强制“召回—度量—淘汰”；Review 面板 |
| 8 周做不完 | 砍 P1；先单语言/单仓库；UI 只做 4 面板 |
| 沙箱复杂 | 先本地受限模式；Docker 可选 |
| 评测不足 | 先 10 个 golden tasks；每周加 2 个 |
| 过早自修改 | v1 禁止；v2 再评估 |

---

## 20. 一句话

**v1 不追求“什么都有”，只追求“一个仓库、一个用户、一条主路径”跑通。**
把记忆、知识、经验合成一个 `KnowledgeService`，把恢复留给会话日志，把执行留给 `ExecutionService`；4 个面板、12 个工具、8 个中间件、15 张表。先好用，再变聪明。
