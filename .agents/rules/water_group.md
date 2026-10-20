# QQ 水群规则 (Water Group)

用户说「在群 `<群号>` (`<群名>`) 开始挂机水群」「继续水群」「接管水群」时，按下面做。
不要探索代码、不要读 `mcp_tools.py`、不要翻 SQLite 试探参数。整条链路已经封好了。

## 0. 一句话接管

```bash
.venv/Scripts/python.exe water-group/daemon/up.py
```

**必须放到后台跑，而且它会一直不返回——这是设计如此，不是卡住。**

```bash
# 正确姿势：后台常驻
<上面的命令>   # run_in_background = true
```

它会按依赖顺序拉起 NapCat → `app.py` → 采集/保活守护，打印状态后**原地进入守护循环**，
永不退出。这个进程就是整个水群的命脉：

> ⚠️ 关掉这个后台任务 = 关掉整个水群。不要 `kill`、不要「清理后台任务」、不要重启它。
> 需要复查状态时跑 `--status`（只读，不碰任何进程）。

**为什么不能 detach 后返回**：本机会话结束时连带清理整棵进程树。
实测 `DETACHED_PROCESS` 和 `CREATE_BREAKAWAY_FROM_JOB` 的子进程**连一行日志都写不出来就被杀**，
`schtasks.exe`、`cmd.exe`、`reg.exe` 都在沙箱黑名单里。唯一可靠的办法就是父进程自己不走。

跑完读输出末尾的「最近唤醒」：

- 有 `[待回]` → 逐条回复（见 §3）
- 全是 `[已回]` → 直接进监听（见 §2）

## 1. 架构：为什么不要自己 wait

守护进程常驻，不依赖你这次工具调用是否超时：

| 进程 | 职责 |
| --- | --- |
| `db_keeper.py` | 常驻 SQLite 连接，钉住 WAL 边车文件；缺了会 `资料读取失败` |
| `app.py` | 后端：Web 47860 / MCP 48777，退出会被守护重拉 |
| `collector.py` | 常驻 `wait_chat_messages`，消息先落盘再给你 |
| `watchdog.py` | HTTP 探活保活 QQ，连续 3 次、跨 30 秒才判死 |
| `supervisor.py` | 重启上面全部；采集器心跳超 300s 也视为卡死 |

冷启动耗时参考：NapCat 注入约 **65 秒**，`app.py` 起端口约 **20 秒**，
采集器建立会话约 **20 秒**。这段时间看到「离线 / 未建立」是正常的，别急着重跑。

**绝不要自己调 `wait_chat_messages`**。会和 collector 抢同一个会话，返回
「此会话已有 Agent 在等待」，而且工具超时会把你的循环打断——这正是旧做法失效的原因。

你只做两件事：**读事件、写回复**。

## 2. 监听

状态都在 `.tmp/`（`TULPA_WG_STATE` 可改）：

| 文件 | 含义 |
| --- | --- |
| `wg_events.jsonl` | 消息流水，每行一条，`id` 递增 |
| `wg_state.json` | 最近一次 wait 的返回，含 `input_batch.id` |
| `wg_session.txt` | 会话 id |
| `wg_ack.txt` | 已落盘游标（**不是**已回复游标） |
| `wg_replied.jsonl` | 已回账本，`reply.py` 成功发送时写 |
| `wg_watch.txt` | `watch.py` 的扫描游标 |
| `wg_pause` | 存在时 collector 暂停采集，别误留 |

### 等消息：用 `watch.py`，不要自己轮询

```bash
.venv/Scripts/python.exe water-group/daemon/watch.py --timeout 240
```

**它会阻塞，直到有人 @ 你或提到关键词，然后立刻打印并返回**（实测新唤醒落盘后 1 秒内返回）。
不要用 `sleep` + `wc -l` 那种轮询——那正是「消息来了半分钟才回」的原因：
collector 其实 1 秒内就落盘了，慢的是 Agent 自己隔一阵才看一次。

| 退出码 | 含义 | 下一步 |
| --- | --- | --- |
| `0` | 打印了唤醒（末行 `WAKE`） | 马上 `reply.py <id> "<正文>"` |
| `2` | 超时，没有新唤醒 | 再调一次，不要改去 sleep |
| `1` | 事件流读不到 | 跑 `up.py --status` |

`--replay` 忽略游标重扫；`--after N` 从指定 id 之后开始。

唤醒条件（服务端已过滤一次，这里复核）：

- `segments` 有 `at` 且 `qq` 等于机器人号
- `segments` 有 `reply`，且被引用的是机器人自己发的
- 正文含 `config.json` 的 `self_names` 任一词（`🐟包` / `鱼包` / `小鱼包` / `鱼宝`）

## 3. 回复

一条命令搞定，**不要手搓 plan/send**：

```bash
.venv/Scripts/python.exe water-group/daemon/reply.py <消息id> "<正文>"
# 想看不带引用的版本加 --no-quote；只列待回：reply.py --list
```

它会自己处理：查消息编号 → OneBot 直发带引用 → 写已回账本。
**每条回复都引用触发的那条消息**（要发纯文本才加 `--no-quote`）。

**为什么引用不走 `send_chat_reply` 的 `quote`**：`plan_chat_reply` 只接受**当前批次**里的 `target_event_id`，而群里每来一条新消息就开一个新批次。等你写好正文，触发你的那条通常已经落在旧批次里了——此时 plan 要么直接 `target_not_in_batch` 失败，要么改投到最新那条、把引用挂到别人头上（实测回 1202 时批次翻到 1203，引用会引到 1203）。所以引用改走 OneBot 原生 `reply` 消息段，用 collector 已存的 `onebot_message_id`，跨多少批次都不受影响。plan 路径保留为兜底。

<details>
<summary>它内部做的事（出问题时才需要看）</summary>

**① 主路径：OneBot 直发带引用**

1. 在 `.tmp/wg_events.jsonl` 里按 `id` 找到目标事件，取 `conversation_id` 里的群号
   和 `onebot_message_id`；
2. `POST {onebot_http_url}/send_group_msg`，`message` 为
   `[{"type":"reply","data":{"id":"<onebot_message_id>"}}, {"type":"text","data":{"text":"<正文>"}}]`；
3. `retcode == 0` 即成功，写账本。

查不到 `onebot_message_id`（事件太老被轮转掉）或发送失败，才走 ②。

**② 兜底：plan + send_chat_reply**

**取 batch_id** — `.tmp/wg_state.json` 的 `input_batch.id`。批次会过期。

**plan**

```
plan_chat_reply(session_id, batch_id, action="reply",
                expressions=["text"], intent="<简短沟通意图>",
                target_event_id=<要回的消息 id>)
```

| 返回 | 处理 |
| --- | --- |
| `READY` | 取 `plan_id`，进入 send |
| `SUCCEEDED` | 该 `(session, batch, target, intent)` 已经规划完成过，会**静默不发**。换一个 intent 重试 |
| `REJECTED` + `stale` / `needs_refresh` | 用 `updated_chat.input_batch.id` 原地重试，不要重新 wait |
| `REJECTED` + `target_not_in_batch` | 批次翻页了。用返回里的 `valid_event_ids` 最后一个当新目标，**同一个 batch** 原地重试；此时不要引用（会引到别人） |
| `REJECTED` + `source_unavailable` | NapCat 正在重连，等 30 秒再试 |
| `REJECTED` + `batch_expired` | 重读 `wg_state.json` 拿最新 batch |
| `REJECTED` + `chat_rejected` | 会话被别的等待者占着，跑 `up.py --status` 看守护 |

**send**

```
send_chat_reply(session_id, plan_id,
                bubbles=[{"kind":"text","text":"...","quote":true}],
                expression_ids=[])
```

`plan_chat_reply` 返回的 `bubbles` 永远是空的，**正文自己写**。

</details>

### 为什么要有 `.tmp/wg_replied.jsonl`

`mention_only` 的批次只在**新的 @** 到来时返回，所以你刚发的那条自发消息通常还不在
`wg_events.jsonl` 里。没有这份账本，下一个接管的 Agent 看报告会把已经回过的消息当成
`[待回]`，再回一遍。`reply.py` 发送成功就记一笔，`up.py` 报告优先读账本。

发完不用写 `wg_ack.txt`，collector 落盘时已经推进了。

## 4. 闲时沉淀群友画像

没被 @ 的时候群里照样在聊，`chat_inbox` 里一条不落——Tulpa 先把每条消息入库，再决定要不要唤醒。素材一直都在，只是没人整理。

```bash
# 该提炼了吗（exit 0 = 该，3 = 还不用）
.venv/Scripts/python.exe water-group/daemon/dossier.py --check

# 取一批素材，最早的优先（分批消化积压，中间不会漏）
.venv/Scripts/python.exe water-group/daemon/dossier.py --dump 60

# 规则版消化：纯关键词，不调模型，每次超时都可以跑
.venv/Scripts/python.exe water-group/daemon/dossier.py --digest

# 一批处理完，把游标推到最后一条的 id
.venv/Scripts/python.exe water-group/daemon/dossier.py --mark --through <id>
```

**触发条件**（`config.json` 的 `dossier` 段）：攒够 `burst_messages`（默认 80）直接触发；或者闲置超过 `idle_minutes`（默认 20）且攒了 `min_messages`（默认 25）。

**两层**：

- `--digest` 走 `memory_engine.digest_idle_batch`，关键词打标（沙雕梗 / 猛男控 / 游戏党）+ 偏好提取，零成本，每次超时都该跑。
- `--dump` → 自己提炼 → `consolidate_chat_memory` → `--mark` 是模型版，质量高但贵，只在 `--check` 说 YES 时做。

**`traits` 必须传 dict**：`update_member_dossier` 里是 `dict(traits) if isinstance(traits, dict) else {}`，传数组会被**静默丢成空**（踩过一次）。写成 `{"性格": "..."}`，会和已有的 `{card, nickname}` 合并，不覆盖。

**同一件事换个说法不会占两格**：`memory_format.merge_text_list` 在合并时用包含关系折叠——短的那条如果已经完整落在长的那条里就丢掉，留信息量大的那条（三字以下不比，否则会误伤「+1」「120」）。记忆上限 10 格、兴趣 16 格，**满了丢旧的**，因为老事实通常后来又被复述过。所以写档案时不用怕重复，写得具体点反而更好。

**监听循环**（`watch.py` 超时即「一段时间没被 @」）：

```bash
.venv/Scripts/python.exe water-group/daemon/listen.py --timeout 300
```

`listen.py` 在静默里跑 `watch.py`，每次超时就 `--digest` 一次再看 `--check`。**它有事就退出**，不常驻：退出码 0 表示「有活」（stdout 末尾是 `WAKE` 或 `DOSSIER_DUE`），1 表示事件流读不了。所以拉起来之后只管等它结束，不要轮询。

## 5. 写回复

- 1~10 字的短句，打字感，不换行；最多两条。
- 禁客服腔、禁排比升华、禁括号内心戏、禁总结和礼貌收尾。
- 人设卡在 `water-group/personas/`（当前 `yu_bao`）。
- 群友档案用 MCP 工具读，**不要直连 SQLite**：`query_member_dossier` /
  `update_member_dossier` / `consolidate_chat_memory`。
- 群友常改名，靠 `config.json` 的 `member_mappings` 和名片同步对齐，别硬认昵称。

## 6. 想更主动

`config.json` 里目标群的 `trigger_mode` 是 `mention_only`：只在被 @ / 引用 / 点名时唤醒。
改成 `natural` 会按 `participation` 参与闲聊，噪音也一起上来。改完跑一次 `up.py` 生效。

## 7. 出问题先看这三处

```bash
tail -20 water-group/logs/wg-supervisor.log   # 谁被重启了
tail -20 .tmp/wg_collector.log                # 采集是否在报错
tail -20 water-group/logs/napcat-watchdog.log # QQ 是否被反复重注入
```

| 现象 | 原因 |
| --- | --- |
| 反复 `资料读取失败` | WAL 竞态，跑 `.venv/Scripts/python.exe .tmp/db_keeper.py` 常驻一个连接 |
| 大量 `Threads=0` 的 QQ 进程 | 崩溃后进程对象没回收，只能重启系统；别再反复重启，watchdog 已熔断 |
| `source_unavailable` 持续 | NapCat 掉线，`up.py` 会重新注入 |
