# 复核报告（4 个并行 subagent）与主文档勘误

> 复核时间：2026-10-21 07:30 GMT+8。全部只读，未改动任何文件。
> 复核对象：`doc/WATER_GROUP_ONLY_PLAN.md`（2026-10-21 核对版）+ 当前工作树（HEAD `ce1a575`）。

---

## 1. 导入闭包复核

结论：**13 处 keep→delete 硬依赖必须在 Phase 2 修**；其中 5 处模块级（import 即崩）、8 处函数内（运行时崩，最易漏测）。

| # | file:line | 依赖 | 层级 | 建议 |
|---|---|---|---|---|
| 1 | `mcp_access.py:9` | `client_accounts.bound_account` | 模块级 | `accounts()` 改为读 `config.json` 的 `self_account`（或进程内缓存），删 import |
| 2 | `mcp_access.py:10` | `retrieval.Plan/date_bound` | 模块级 | **`plan()` 不能删**（5 处 keep 调用）；保留 35 行 `retrieval.py` 或内联 |
| 3 | `mcp_access.py:71` | `terminal_instances.validate_mcp_port` | 函数内 | 删该行或换 5 行端口占用检查 |
| 4 | `mcp_actions.py:9` | `group_admin.GroupAdmin` | 模块级 | 群管理分支整段删除，或 import 移入分支内 |
| 5 | `mcp_actions.py:12` | `retrieval.scope_sql` | 模块级 | 删 `scoped_target`（水群发送走 `mcp_chat.py:960` 的 `target_resolver`） |
| 6 | `mcp_routes.py:47` | `desktop_routes.read_product_settings` | 函数内 | 改用 `message_sender.sender_config()`；**勿复制** `desktop_routes.py:20`（它自己 import snowluma） |
| 7 | `mcp_routes.py:102` | `mcp_connect.test_connection` | 函数内 | 删 `/api/mcp/test` 路由（daemon 不调用） |
| 8 | `mcp_routes.py:115` | `mcp_connect.install_codex` | 函数内 | 删 `/codex` 路由（GUI 专用） |
| 9 | `message_sender.py:23` | `snowluma_managed.connection` | 函数内 | `sender_config()` 改读 `water-group/config.json` |
| 10 | `message_sender.py:56` | `snowluma_managed.guard` | 函数内 | 内联 no-op；`_managed` 字段唯一消费点 |
| 11 | `onebot.py:37/65` | `connection`/`guard` | 函数内 | 内联 no-op；`configuration()` 改 config.json 优先、`.env` 回退 |
| 12 | `onebot_events.py:20/161` | `connection`/`guard` | 函数内 | 同上（L161 每条 WS 事件都走） |
| 13 | `store.py:8` + `:11-12` + 18 处 lazy | `jieba`(顶层)/`normalize`/`media`/`image_identity` 等 | 模块级/函数内 | 整体替换为 WGStore（见 Phase 2）。**`:8 顶层 import jieba` 必须删**（Phase 3 删 jieba 包会让 `import chatlocal.store` 直接 ImportError，连锁崩 `mcp_chat_local_media`/`mcp_tools`/`mcp_routes`）；lazy 引用 `qq_labels`/`qq_storage`/`read_options`/`tulpa`/`import_pipeline`/`sync_state`/`live_state` 保留无害（水群不调这些方法） |

其他关键发现：

- `mcp_chat.py` 有 6 处 lazy import `water_group.memory_engine`；其中 `:1004/:1017/:1033` **没有 try/except**，dossier 工具会直接报错。
- **§4.2 保留媒体 ↔ §4.3 删 `media`/`image_identity` 自相矛盾**：`mcp_chat_local_media.py:30,44` 依赖两者，`media.py` 又依赖 `normalize.safe_reply` → 三个模块必须随媒体链保留。
- `mcp_tools.py` 未出现在 §4.1/§4.3 任何清单，但被 `mcp_server.py:19` 硬引用（`MCPTools`/`INSTRUCTIONS`）。Phase 2 原地改写为 `WGTools` 时必须迁 `INSTRUCTIONS`（实际定义在 `mcp_tools.py:66`，非 `:42`）。**新增**：`mcp_tools.py` 还顶层引用 4 个删除集模块——`:18 activity`、`:19 agent_tools`、`:123 qq_tools`(lazy)、`:296 vision`(lazy)——必须在 Phase 2 §2.3 一并处理，否则 Phase 3 删它们时 import 崩。其中 `agent_tools` 同时被 `check_group_memory.py:445` 依赖，删前必须改门禁 + 改 mcp_tools 顶层 import。
- `reply_routes.py` 全仓无 keep 引用（keep 集模块不引用它；删除集的 `app.py`/`terminal_server.py` 引用，随壳删），属漏列文件，可直接删。
- **WGStore 不建 `messages` 表** → `mcp_routes.py:46/78/243`、`mcp_access.py:118`、`mcp_actions.py:55` 会 500。必须建同 schema 空表。

## 2. 运行时链路复核

结论：**Phase 1 用 `server_wg.py` 替换 `app.py` 在端点层面是安全的（0 个 404）**——daemon 对 47860 的调用全部落在 `mcp_routes.py`；但入口指向、MCP 监听、日志三处必须同步改，否则“换入口”实际不会生效。

- daemon 端点清单（全部可用）：
  - `POST /mcp`（48777，initialize/tools call/ping）→ `mcp_server.py:183`
  - `POST /api/mcp/connections` → `mcp_routes.py:68`（`mint_grant.py:53` 调用）
  - `GET /api/mcp/chats`、`POST /api/mcp/chats/{sid}/stop` → `mcp_routes.py:133/228`（`session.py:53/64` 调用）
  - OneBot 43000 系列与 Tulpa 无关。
- **48777 由 `install_mcp_routes` 的 lifespan 在进程内起 uvicorn 线程**（`mcp_routes.py:18-28`、`mcp_server.py:185-230`）。`server_wg.py` 不需要显式启动 MCP，但**绝不能覆盖 `app.router.lifespan_context`**，且必须保证 `settings` 行 `enabled=1, port=48777`。
- **新库/清空 `data/` 后 `settings` 默认 `(0, 18777)`**（`mcp_access.py:42-43`）→ MCP 静默不启动，daemon 全体 401/拒连。Phase 1 必须自检修正。
- 三处仍硬编码 `app.py`：`supervisor.py:41`、`bootstrap.py:431`（`ensure_tulpa` 会再起一个 app，抢端口）、`start.ps1:12` / `stop.ps1:2`。
- 子进程 stdout/stderr 全部 `DEVNULL`（`supervisor.py:74-78`）→ Phase 1 任何 ImportError 不可见。
- **`requests` 不在 `requirements.txt`**，但 daemon 4 个文件依赖（`bootstrap.py:9`、`sync_groups.py:5`、`reply.py:32`、`up.py:?`），目前只是 gradio 的传递依赖。gradio 一删，daemon 全链路 ImportError。
- `fwd.py` 硬转发 18777→48777（`bootstrap.py:448` 拉起），与真实 48777 冗余；删壳时一并删。
- token 单槽 `.tmp/mcp_token.txt` 被多 Agent 共用；实测当前 token 属 `workbuddy-session` 而非 `water-group`。401 自愈链必须保留。
- grant 绑定 `data/chats.sqlite3` 的 `local_change_meta.epoch`；**Phase 2 不要“顺手修 epoch”**，否则全量 401。

## 3. scripts 与门禁复核

结论：**§8 的“≈24 个”不成立**——至少 3 个被标“全程”的门禁顶层 import 删除集模块；另有 3 个夹具脚本 + `reply_timing_report.py` 是保留门禁的硬依赖；`check_mcp_chat_targets.py`/`check_mcp_chat_prompts.py` 是覆盖缺口。

- 顶层 import 删除集、Phase 3 必红：`check_mcp.py`（`agent_tools`/`artifacts`）、`check_mcp_onebot.py`（`desktop_routes`/`mcp_connect`）、`check_mcp_chat.py`（`agent_tools`/`mcp_connect`，L209-211）、`check_group_memory.py:445`（`agent_tools.tool`）。
- 隐含依赖：`check_water_group_import.py:45` 断言 `__all__` 含 `sync_personas` → Phase 3 删该文件必须同步改断言。
- 夹具刚需：`fixture_server.py`、`check_fixture_support.py`、`chat_adapter_fixture.py` 被 15+ 门禁 import。
- 覆盖缺口：`check_mcp_chat_targets.py`（@/引用投递主链，零删除集依赖）、`check_mcp_chat_prompts.py`（人格卡装配，R9 必跑）。
- daemon 运行期对 `scripts/` 零依赖；`start-reply-sender.ps1`/`stop-reply-sender.ps1` 只服务 SnowLuma，随壳删。
- **最终保留 27 个**（23 门禁 + 4 刚需），删除 164 个（191−27）。

## 4. 数据/配置复核

结论：**§5.2 表清单不完整；附录 B 的 40 行 WGStore 不够用；§5.1“`.env` 可整体删除”不成立**。

- §5.2 缺失：`messages`、`operations`/`operation_events`、`local_change_meta`、`sticker_*`（6 张，**无 `chat_` 前缀**）、`sticker_catalog`、`qq_friend_requests`/`qq_media_assets`；以及独立库 `data/mcp-language.sqlite3`（25+ 表）与 `data/mcp-chat-media/`、`data/mcp-chat-library/`、`*-policy.json`。
- `client_settings` 只被删除集使用 → 可从合同去掉。
- WGStore 实际需要 ≈55-70 行：`path`、`connect(timeout=30, foreign_keys=True, Row)`、WAL、`local_change_meta`、`messages` 同 schema、**`import_file()`**（4 个门禁靠它播种）、`tokens()` 兼容存根（Phase 3 再删）。
- `local_change_meta` 现在建在 `data/chats.sqlite3`（`changes.py:7`），附录 B 把它挪到 `mcp-access.sqlite3` 会改变 epoch 语义 → 必须建在 `chats.sqlite3`。
- `.env` 的 `REPLY_ONEBOT_URL/TOKEN/WS_URL/WS_TOKEN/WS_TOKEN_MODE` 被 keep 模块读取（`onebot.py:41-47`、`message_sender.py:28-29`、`onebot_events.py:24-30`）→ **不可删**；只有 `API_*`/`AGENT_*`/`VISION_*`/`VOICE_*` 可删。
- `onebot.configuration()` 目前**不读** `config.json` → §5.1 的前提改造不存在，必须先实现再谈精简 `.env`。
- `.tmp` 合同不止 6 个：还有 `wg_state.json`、`wg_heartbeat`、`wg_dossier.json`、`wg_pause`（已是死契约）、`wg_collector.log`、`mcp_token.txt`；全部尊重 `TULPA_WG_STATE`。
- 人格卡有**双写暗路径**：`mcp_chat.py:253-268 sync_session_persona` 也会往 `prompts.ROOT` 复制，Phase 3 切 ROOT 时必须一并改。
- `check_mcp_chat_prompts.py:80-81` 硬读 `little_whale.source.json`，切 ROOT 后会挂。

## 5. 对主文档的勘误（执行时以本目录为准）

| # | 主文档位置 | 原文 | 修正 |
|---|---|---|---|
| E1 | §3.3、§6 Phase 2-3 | “删除 `plan()` 与 `retrieval`” | **保留 `plan()`**；保留 `retrieval.py` 的 `Plan/date_bound/scope_sql`（≈35 行）或内联进 `mcp_access` |
| E2 | §4.3 | 删 `media`、`image_identity`、`normalize` | 三者**随媒体链保留**（`mcp_chat_local_media.py:30,44`、`media.py` 依赖 `normalize.safe_reply`）；补 `mcp_tools.py`（原地改写）、`reply_routes.py`（无引用可删） |
| E3 | §5.1 | “`.env` 可整体删除” | 保留 `REPLY_ONEBOT_*` 5 键；只删 `API_*`/`AGENT_*`/`VISION_*`/`VOICE_*`/`MODEL`/`QQ_CACHE_MODE`/`ARTIFACT_ONEBOT_*`；先把 `onebot.configuration()` 改成 config.json 优先 |
| E4 | §8、§11.7 | scripts 保留 ≈24 个 | **27 个**（+`check_mcp_chat_targets`、`check_mcp_chat_prompts`、3 夹具、`reply_timing_report`）；6 个门禁需改 import（注：`check_water_group_import.__all__` 同时要去 `sync_personas` 和 `fwd`；`check_mcp_chat.py:211` 硬编码 18777 需同步改 48777） |
| E5 | 附录 B | WGStore ≈40 行 | ≈55-70 行；必须有 `messages`/`import_file`/`tokens` 存根；`timeout=30` + `foreign_keys`；`local_change_meta` 建在 `chats.sqlite3`；**删顶层 `import jieba`（`:8`）** |
| E6 | §6 Phase 1 | 只改 supervisor 指向 | 另需：`settings` 自检（enabled=1/port=48777）、子进程日志、`bootstrap.py:431`、`start.ps1`/`stop.ps1`、`requirements.txt` 加 `requests` |
| E7 | §5.2 | 表清单 | 补 `messages`、`operations*`、`local_change_meta`、`sticker_*`（裸前缀 7 张）、`mcp-language.sqlite3`；删 `client_settings` |
| E8 | §3.3、R2 | epoch 返回常量 | **Phase 2 保持读表**（值不变）；改常量会触发全量 401，留到 Phase 4 评估 |
| E9 | §5.2 人格卡 | 切 ROOT + 删 sync_personas | 另需迁移 `behavior.md`/`examples.json`/`*source.json`、改 `mcp_chat.py:253` 暗路径、改 `check_water_group_import` 与 `check_mcp_chat_prompts` 断言 |
| E10 | §2 | 进程树 | 补 CLI 面（reply/listen/watch/followup/dossier/session/mcp_client）与 `mcp_token.txt` 单槽风险 |

## 6. 复核新增风险

| # | 风险 | 后果 | 缓解 |
|---|---|---|---|
| R11 | `server_wg.py` 覆盖 lifespan 或 settings 未开 | 48777 静默不监听，daemon 全 401，`up.py --status` 仍显示端口在 | Phase 1 自检 + 冷启动验证 + 显式 exit 非 0 |
| R12 | WGStore 早于 Phase 3 替换，删除集模块仍 import `store.Store`/`tokens` | 门禁/旧模块 ImportError | 保留类名 `Store` 与 `tokens()` 存根，Phase 3 收尾再删 |
| R13 | `check_mcp*` 顶层 import 删除集 | Phase 3 验收假绿/必红 | Phase 3 按 §3 清单改 6 个门禁 import（**注：`check_water_group_import.py:45-47` 的 `__all__` 同时含 `sync_personas` 和 `fwd`，删两者都要同步改**） |
| R14 | `requests` 未入 requirements | gradio 一删 daemon 全挂 | Phase 1 先加（3 个文件：bootstrap/sync_groups/reply；`up.py` 用 stdlib urllib 不需要） |
| R15 | token 单槽被多 Agent 覆盖 | collector 被踢 401，自愈依赖 mint | 保留 401 重铸；Phase 2 起按 grant 名分文件（可选） |
| R16 | 人格卡双写暗路径 | 切 ROOT 后旧卡复活/覆盖 | Phase 3 同改 `mcp_chat.py:253`，先清点两侧目录 |
