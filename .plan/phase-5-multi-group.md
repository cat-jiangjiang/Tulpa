# Phase 5 · 多群布局

> 状态: **待执行**（2026-10-22 立）
> 依据: `doc/MULTI_GROUP_PLAN.md`（v2，4 路并行勘察后的修订版）
> 目标: 一台机器挂 N 个群，每群一套「采集器 + 监听 + Agent」，互不可见；后端 / NapCat / watchdog / db_keeper 仍各一份。
> 约束: 本文档遵循 `00-overview.md` §4.3，不写真实 QQ 号 / 群号 / 人名。

---

## 1. 决策

| # | 决策 | 结论 |
|---|---|---|
| D1 | 群身份载体 | 每个群一个状态目录 `.tmp/g<群号>/`，目录内新增 `wg_conversation.txt` 作为**唯一权威**；环境变量只作覆盖 |
| D2 | 进程拓扑 | 每群一个 `collector.py`，共用一个 `supervisor.py`；`db_keeper` / `app` / `watchdog` 保持单例 |
| D3 | Agent 绑定 | 每群一个独立 Agent 会话，靠 `--group`（或 `--state`）绑定，不依赖 `export`（工具调用不跨 shell） |
| D4 | 授权 | **每群一个 grant**（每状态目录一份 `mcp_token.txt`），`mint_grant.NAME = water-group-<群号>` |
| D5 | 规模上限 | N ≤ 8，由 `chat_slots` 与活跃会话数两条硬上限决定，本期不改上限 |
| D6 | PR #1 取舍 | 留 `state_dirs.py`（改语义）；不做 `watchall.py`；`fudeng.py` / `img.py` 与本期无关 |

D3 的理由：`reply.py --wait` 会拉 `listen.py`，若靠环境变量，每次工具调用都得重设一次，漏一次就落到根 `.tmp`，多群下就是串群。

---

## 2. 目录布局

```
.tmp/
  g<群号>/
    wg_conversation.txt     # 新增：<账号>:group:<群号>
    wg_session.txt  wg_events.jsonl  wg_ack.txt  wg_state.json
    wg_watch.txt  wg_replied.jsonl  wg_followup.json  wg_dossier.json
    wg_heartbeat  mcp_token.txt  wg_collector.log
  g<群号>/
    ...
```

兼容：根目录有 `wg_session.txt` 且无 `g*/` 时，根目录整体视为「一个群」，行为与现在一致。这条回退是升级安全的前提。

---

## 3. 步骤

### S1 · 群身份与会话查询（不改进程模型，单群行为不变）

| 子步 | 文件 | 改动 |
|---|---|---|
| S1.1 | `water-group/daemon/state_dirs.py`（新增） | `groups()` 读 `config.json` 的 `enabled != False`；`ensure(gid)` 建目录并写 `wg_conversation.txt`；`resolve(group=None, state=None)` 供 CLI 用；单群布局从 `wg_conversation.txt` 取群号，**不返回 `default` 标签** |
| S1.2 | `daemon/session.py:19-20` | `GROUP` 三级解析：`TULPA_WG_CONVERSATION` → `<state>/wg_conversation.txt` → 报错退出；**删除硬编码默认群号**。`PERSONA` 按群读 `config.groups.<gid>.persona`，回退 `default_persona` |
| S1.3 | `daemon/dossier.py:72`、`daemon/followup.py:88`、`daemon/reply.py:465` | 统一改为 `WHERE active=1 AND conversation_id=? ORDER BY rowid DESC LIMIT 1`；`conversation_id` 从本群状态目录取 |
| S1.4 | `daemon/reply.py:482` | `sid = session_id() or active_session_id()`（顺序反转） |
| S1.5 | `daemon/reply.py:587`、`daemon/followup.py:112` | `open_followup` 传当前 `sid`；`followup.open(event_id, session=sid)`，窗口显式绑群 |
| S1.6 | `daemon/reply.py:715` 之前 | **P0 护栏**：`event_target()` 取到的群号与本群不符，或查不到归属，直接报错退出，禁止进入 `plan()`。堵住 `:149-160` 的 `allowed[-1]` 重定向 |
| S1.7 | `daemon/collector.py:73-82,96-98` | `bootstrap_session()` 重试循环补 `beat()`；`wg_pause` 分支也写心跳（否则 300 秒被判死） |

**验收**：现有单群栈 `up.py --status` 输出与改动前逐行一致；`check_wake_ledger`、`check_followup`、`check_reply_context`、`check_water_group_import` 全绿。
**回滚**：单文件 `git revert`，无数据改动。

### S2 · CLI 绑定

| 子步 | 文件 | 改动 |
|---|---|---|
| S2.1 | `daemon/watch.py`、`daemon/listen.py`、`daemon/up.py` | 新增 `--group <群号>` / `--state <目录>`；`--group` 解析成 `<根>/g<群号>` |
| S2.2 | `daemon/reply.py:636,670` | 现有 `--group` 语义扩展到「同时决定状态目录」，与 S2.1 统一 |
| S2.3 | `daemon/reply.py:613-618` | `--wait` 拉起 `listen.py` 时透传 `--group` / `--state` |
| S2.4 | `daemon/up.py:163-171` | `listener_pids()` 按 argv 里的 `--group` 区分群；`report()` 支持单群与全群两种输出 |

**验收**：手工建两个 `g<群号>/` 目录，分别 `watch.py --group A` / `--group B`，互不唤醒；`reply.py --group A` 只写 A 的账本。
**回滚**：新参数缺省时行为与现在一致，可只回退参数解析段。

### S3 · 进程模型

| 子步 | 文件 | 改动 |
|---|---|---|
| S3.1 | `daemon/supervisor.py:43-48` | `CHILDREN` 拆 `SHARED`（db_keeper / app / watchdog）与 `PER_GROUP`（collector）；按群 spawn，env 带 `TULPA_WG_STATE`，argv 带 `--state` |
| S3.2 | `daemon/supervisor.py:27,86,120` | 心跳按群查 `g<群号>/wg_heartbeat`；日志 `collector-<群号>.log`；判活条件从 `name == "collector"` 改为「该群采集器」 |
| S3.3 | `daemon/up.py:174-198` | `--stop` 保持全停；新增 `--stop --group <群号>` 只停该群采集器 |
| S3.4 | `water-group/start-bot.pyw:54` | 幂等判断补查 48777，避免「HTTP 在、MCP 挂」被判为已运行 |

**验收**：两群同时挂，各自 @ 只唤醒各自 Agent；杀掉 A 的采集器只重启 A；`--stop --group A` 后 B 仍工作。
**回滚**：按子步 commit 逐个 revert；`start-bot.pyw` 的端口检查可单独回退。

### S4 · 文档与授权收尾

| 子步 | 文件 | 改动 |
|---|---|---|
| S4.1 | `AGENTS.md` §0、`.agents/rules/water_group.md`、`.agents/skills/water-group/SKILL.md`、`water-group/daemon/README.md` | 补「一群一状态目录、一群一 Agent、`--group` 绑定」；把 `.tmp/` 的绝对表述改成 `.tmp/g<群号>/` |
| S4.2 | `water-group/bootstrap.py:530-537` | `ready_groups[0]` 改为列出全部群；`--group` 保留筛选 |
| S4.3 | `daemon/mint_grant.py:21,49-51` | `NAME` 带群号；`last_scope()` 的 `ORDER BY id DESC` 改 `ORDER BY created_at DESC`（`id` 是 uuid，字典序不是时间序） |
| S4.4 | 文档债（见 §6） | 逐条修正，不夹带其他改动 |

**验收**：`check_publication` 绿；`check_bootstrap_health` 绿；文档里不再出现「唯一状态目录 `.tmp/`」的单群表述。
**回滚**：文档回退，无代码影响。

---

## 4. 门禁顺序

按 `00-overview.md` §4.1，本 Phase 只跑相关子集，顺序不变：

1. 纯函数：`check_plan_recovery` → `check_reply_quote` → `check_reply_timing`
2. 结构：`check_memory_engine_imports` → `check_water_group_import` → `check_bootstrap_health`
3. daemon 契约：`check_wake_ledger` → `check_followup` → `check_reply_context`
4. 记忆：`check_group_memory`
5. 发布：`check_publication`

每个子步单独 commit + 单独门禁，红灯只回退该 commit。

---

## 5. 验收矩阵（多群）

| 场景 | 期望 |
|---|---|
| `up.py --status` | 列出全部群，每群独立会话 / 游标 / 心跳 / 监听 pid |
| 心跳 | 每个 `g<群号>/wg_heartbeat` 300 秒内更新 |
| A 群 @ | 只有 A 的采集器落盘，只有 A 的 Agent 被唤醒；B 的 `wg_events.jsonl` 不变 |
| 跨群回复 | 拿 B 群的 id 用 A 群回复 → 必须报错退出（S1.6 护栏），不得发出 |
| 追问窗口 | A 回复后开窗，B 新建会话不会关掉 A 的窗 |
| 采集器崩溃 | 只重启该群，其他群不受影响 |
| 按群停止 | `--stop --group A` 后 B 继续收发 |
| 规模 | 第 9 个群必须明确报 `chat_busy` / `最多同时开启8个群聊`，不得静默卡住 |

---

## 6. 已知文档债（S4.4 逐条修）

| 位置 | 问题 |
|---|---|
| `SKILL.md:3,8,311` | 声称支持多群并让人调 `invoke_subagent`；该函数全仓零引用 |
| `SKILL.md:26` | 写「默认 3 轮或 60 秒」，实际 `followup.py:38` 是 10 条 / 180 秒 |
| `SKILL.md:68` | 写读 `.env` 的 `REPLY_ONEBOT_URL`，实际读 `config.json`（`reply.py:197-206`） |
| `SKILL.md:73` | 说 `sync_groups.py` 已删；它仍在，且被 `bootstrap.py:448-451` 调用 |
| `SKILL.md:28,264` | 说「继续轮询 `wg_events.jsonl`」，与 `watch.py` 的阻塞式设计相反 |
| `rules/water_group.md:366` | `.tmp/db_keeper.py` 路径不存在，实际是 `water-group/daemon/db_keeper.py` |
| `rules:105`、`SKILL.md:127-131` | 说 `watch.py` 退出码 1 = 事件流读不到；实际只返回 0 和 2 |
| `water-group/README.md:16-18` | `stickers/`、`sync_personas.py` 都不存在 |
| `AGENTS.md:224` | 说 `start-bot.pyw`「会一直不返回」；实际 `Popen` 后立即返回，常驻的是 `up.py` |

---

## 7. 不做

- 不改 `chat_slots` / 活跃会话数上限（8）。超过 8 个群需要单独评估线程池与 SQLite 连接压力。
- 不做多账号（`self_account` 仍是单值，`start_chat_session` 要求账号一致）。
- 不做 `watchall.py`：那是「一个 Agent 管所有群」，与 D3 相反。
- 不改表结构、不改 DB 文件名、不动 `data/`。
- 不把 `.plan/` 里的真实群号写进文档。

---

## 8. 风险

| 风险 | 触发条件 | 处置 |
|---|---|---|
| 心跳路径错位 | S3.2 只改了 spawn 没改判活 | 心跳缺失时 `heartbeat_age()` 返回 `10**9`，会形成 15 秒重启循环；S3 验收必须先看日志再放行 |
| 共享 token 触发 409 | 某群漏写 `mcp_token.txt`，回落到根目录 token | 启动时校验每个群目录的 token 存在且互不相同 |
| 串群查询漏改 | 只改了三处中的一处 | 门禁 `check_reply_context` / `check_followup` 需补「两条 active 会话」用例（当前 fixture 只有一 active 一 inactive，测不出问题） |
| `wg_pause` 死锁 | 有人手工创建该文件 | S1.7 已让 pause 期间继续写心跳；文档同步删除「pause 会停心跳」的描述 |

---

## 9. 与 PR #1 的关系

| 文件 | 处置 |
|---|---|
| `daemon/state_dirs.py` | 保留，按 S1.1 改语义后作为本 Phase 基础 |
| `daemon/watchall.py` | 不做（见 §7） |
| `daemon/fudeng.py` | 与本期无关；若合并需先修「单群 label 当群号」和「`[非文本消息]` 占位符」两个 bug |
| `daemon/img.py` | 建议退回：`read_chat_image` 已能返回像素且带撤回 / 授权校验 |
