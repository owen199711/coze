# ADR-0003: 权限模式在工具层强制执行（default 写前审批，dont_ask 跳过）

- **状态**: Accepted
- **日期**: 2026-11-19
- **决策者**: Atlas 维护者
- **相关**: `src/atlas/tools.py` `AtlasTools.call()`、`src/atlas/config.py` `VALID_PERMISSION_MODES`、`tests/test_safety.py`

## 背景

`AtlasConfig.permission_mode` 有四个合法值（`config.py` `VALID_PERMISSION_MODES`）：
`default`、`accept_edits`、`explore`、`dont_ask`。

但代码里**只有 `explore` 有实际策略**（`tools.py` 三处 `permission_mode == "explore"`）。
`default`、`accept_edits`、`dont_ask` 三者行为完全相同：写操作直接执行，没有任何审批。

这与设计意图不符：

- `docs/atlas-coding-agent-design.md` 的 `defaults.permission_mode: default` 与
  `permissions.allow/deny` 规则表明 `default` 是"需要确认"的模式。
- `dont_ask` 的命名明确表示"不询问"，即跳过审批——若与 `default` 无差别，该模式无意义。

用户报告"permission_mode 只写进 prompt，没有审批/策略执行"。经实测，`explore` 的
拦截（改文件、读 `.env`/`.git`、`git add`/`commit`）**已经生效**；真正缺失的是
`default` 的写前审批与 `dont_ask` 的区分。

## 决定

1. **在 `AtlasTools.call()` 统一加审批门**，而非散落在各 handler：
   对**非只读**工具（`ToolSpec.read_only is False`）在 `default` 模式下调用
   `ask_user_cb` 请求确认；用户拒绝则返回 `ToolResult("error", ...)`，不执行 handler。
2. **`dont_ask` 跳过审批**：直接执行（与 `accept_edits` 相同，但语义显式）。
3. **`accept_edits` 不审批**：保持现状（编辑类操作自动接受）。
4. **`explore` 不变**：仍由现有 handler 级检查拦截写操作。
5. **无 `ask_user_cb` 时 `default` 拒绝写操作**：返回明确错误，提示切换到
   `accept_edits`/`dont_ask` 或提供交互回调。安全默认（fail-closed）。

## 后果

**收益**

- 四个权限模式语义清晰、可区分，`default` 真正"写前确认"。
- 审批集中在 `call()` 一处，新增工具自动继承策略，无需逐个改 handler。

**代价 / 推迟**

- 按 `AGENTS.md` "加一个能力、移除或推迟一个旧东西"：**推迟**设计文档中的
  `permissions.allow/deny` 规则引擎（按 tool+pattern 匹配），本 ADR 只做模式级审批。
- `default` 模式下无回调的自动化场景（如 CI）会失败，需显式配置 `accept_edits`/`dont_ask`。

**风险**

- 现有依赖 `default` 静默写文件的脚本会开始报错。缓解：错误信息给出明确切换指引。

## 备选方案

| 方案 | 结论 |
|---|---|
| A. `call()` 统一审批门 + `dont_ask` 跳过（本 ADR） | **采纳** |
| B. 每个写 handler 各自加审批 | 不采纳：重复、易漏 |
| C. 实现完整 allow/deny 规则引擎 | 推迟：范围过大，先做模式级 |
| D. 维持现状，仅补文档 | 不采纳：用户明确要求策略执行 |

## 验证计划

1. 先写失败测试（`tests/test_safety.py`）：
   - `test_default_mode_requires_approval`：`default` 模式下 `edit` 触发 `ask_user_cb`，
     拒绝时返回 error 且文件未改。
   - `test_default_mode_without_callback_denies_writes`：无回调时写操作返回 error。
   - `test_dont_ask_mode_skips_approval`：`dont_ask` 下写操作不调用回调、直接成功。
   - `test_accept_edits_mode_skips_approval`：`accept_edits` 下不调用回调。
2. 实现后 `pytest -q` 全绿。
