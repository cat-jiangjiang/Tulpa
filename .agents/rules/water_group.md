# QQ 水群规则 (Water Group)

用户说「在群 `<群号>` (`<群名>`) 开始挂机水群」「继续水群」「接管水群」时，按下面做。
不要探索代码、不要读 `mcp_tools.py`、不要翻 SQLite 试探参数。整条链路已经封好了。

## 0. 一句话接管

```bash
.venv/Scripts/python.exe water-group/daemon/up.py
```

按依赖顺序拉起 NapCat → 后端 `app.py` → 守护 `supervisor`，然后打印状态。
已就绪的会跳过，所以随时可以再跑一次。只看不动加 `--status`。

跑完读输出末尾的「最近唤醒」：

- 有 `[待回]` → 逐条回复（见 §3）
- 全是 `[已回]` → 直接进监听（见 §2）

## 1. 架构：为什么不要自己 wait

守护进程常驻，不依赖你这次工具调用是否超时：

| 进程 | 职责 |
| --- | --- |
| `collector.py` | 常驻 `wait_chat_messages`，消息先落盘再给你 |
| `watchdog.py` | HTTP 探活保活 QQ，连续 3 次、跨 30 秒才判死 |
| `supervisor.py` | 重启上面两个；采集器心跳超 300s 也视为卡死 |

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
| `wg_pause` | 存在时 collector 暂停采集，别误留 |

等新消息用普通命令轮询文件，不要用 MCP 等待：

```bash
base=$(wc -l < .tmp/wg_events.jsonl)
for i in $(seq 1 56); do
  [ "$(wc -l < .tmp/wg_events.jsonl)" -gt "$base" ] && break
  sleep 5
done
```

唤醒条件（服务端已过滤一次，这里复核）：

- `segments` 有 `at` 且 `qq` 等于机器人号
- `segments` 有 `reply`，且被引用的是机器人自己发的
- 正文含 `config.json` 的 `self_names` 任一词（`🐟包` / `鱼包` / `小鱼包` / `鱼宝`）

## 3. 回复

三步，缺一不可。

**① 取 batch_id** — `.tmp/wg_state.json` 的 `input_batch.id`。批次会过期。

**② plan**

```
plan_chat_reply(session_id, batch_id, action="reply",
                expressions=["text"], intent="<简短沟通意图>",
                target_event_id=<要回的消息 id>)
```

必须检查 `state`：

| 返回 | 处理 |
| --- | --- |
| `READY` | 取 `plan_id`，进入 ③ |
| `REJECTED` + `stale` / `needs_refresh` | 用 `updated_chat.input_batch.id` 原地重试，不要重新 wait |
| `REJECTED` + `source_unavailable` | NapCat 正在重连，等 30 秒再试 |
| `REJECTED` + `batch_expired` | 重读 `wg_state.json` 拿最新 batch |
| `REJECTED` + `chat_rejected` | 会话被别的等待者占着，跑 `up.py --status` 看守护 |

**③ send**

```
send_chat_reply(session_id, plan_id,
                bubbles=[{"kind":"text","text":"...","quote":true}],
                expression_ids=[])
```

`plan_chat_reply` 返回的 `bubbles` 永远是空的，**正文自己写**。
被 @ 或点名时首条带 `"quote": true`，开 QQ 原生引用。

发完不用写 `wg_ack.txt`，collector 落盘时已经推进了。

## 4. 写回复

- 1~10 字的短句，打字感，不换行；最多两条。
- 禁客服腔、禁排比升华、禁括号内心戏、禁总结和礼貌收尾。
- 人设卡在 `water-group/personas/`（当前 `yu_bao`）。
- 群友档案用 MCP 工具读，**不要直连 SQLite**：`query_member_dossier` /
  `update_member_dossier` / `consolidate_chat_memory`。
- 群友常改名，靠 `config.json` 的 `member_mappings` 和名片同步对齐，别硬认昵称。

## 5. 想更主动

`config.json` 里目标群的 `trigger_mode` 是 `mention_only`：只在被 @ / 引用 / 点名时唤醒。
改成 `natural` 会按 `participation` 参与闲聊，噪音也一起上来。改完跑一次 `up.py` 生效。

## 6. 出问题先看这三处

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
