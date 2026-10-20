# QQ 水群规则 (Water Group)

用户说「在群 `<群号>` (`<群名>`) 开始挂机水群」「继续水群」「接管水群」时，按下面做。
不要探索代码、不要读 `mcp_tools.py`、不要翻 SQLite 试探参数。整条链路已经封好了。

## 0. 一句话接管

```bash
.venv/Scripts/python.exe -c "import subprocess,pathlib; subprocess.Popen(['explorer.exe', str(pathlib.Path('water-group/start-bot.pyw').resolve())])"
```

**不要直接跑 `up.py`。** 它确实是整栈的入口，但必须由 explorer 代启动：代理的沙箱给每个
子进程注入 `tsbx.dll`，注入沿 up.py → supervisor → watchdog → NapCatWinBootMain → QQ
一路继承，QQ 被注入后约 20 秒必崩（`0xC06D007F`，delay-load 的 `ERROR_PROC_NOT_FOUND`），
watchdog 于是无限重注入——桌面上那堆黑框就是这么攒出来的。

`start-bot.pyw` 是 `.pyw`（关联 `pythonw.exe`，无控制台），经 explorer 拉起时整条链干净。
它幂等：端口已在就什么都不做；否则先 `up.py --stop` 退掉旧栈再起新的。

**必须放到后台跑，而且它会一直不返回——这是设计如此，不是卡住。** 需要看输出时读
`water-group/logs/launcher.log`。

```bash
# 正确姿势：后台常驻
<上面的命令>   # run_in_background = true
```

它会按依赖顺序拉起 NapCat → `app.py` → 采集/保活守护，然后进入守护循环**永不退出**。
这个进程就是整个水群的命脉：

> ⚠️ 关闭它只能用 `up.py --stop`（`stop.ps1` 只管 `app.py`，停不干净；`--stop` 自己也可能留残留，见下）。
> 不要 `kill`、不要「清理后台任务」、不要重启它。复查状态跑 `up.py --status`（只读）。

### 重启整栈：`--stop` 不等于全停

`up.py --stop` 只退 supervisor / 看护 / 注入实例，**`app.py` 与 `collector.py` 常常原地活着，继续占着 47860 / 48777**。而 `start-bot.pyw` 是按端口判幂等的——端口还在就认定「已在运行」直接退出。结果：QQ 被杀没拉回来，`up.py --status` 却仍显示「后端 在线」，群里彻底哑掉还不能自愈。

重启前先确认端口真的空，再起启动器：

```bash
netstat -ano | grep LISTENING | grep -E ":47860|:48777"   # 还有输出就是没停干净
```

有残留就按 PID 精准清（PowerShell 的 `Stop-Process -Id <pid> -Force`；Git Bash 里 `taskkill /F` 的 `/F` 会被当路径转义吃掉），端口空了再从 explorer 起 `start-bot.pyw`。实测 QQ 主进程没死时冷启 **20 秒**就绪，结束后重挂监听。

**为什么必须绕开代理进程**：本机会话结束时连带清理整棵进程树，而代理的沙箱还会给每个子进程
注入 `tsbx.dll`。实测在代理里直接起的 `DETACHED_PROCESS` 子进程连一行日志都写不出来就被杀；
`schtasks.exe`、`cmd.exe`、`reg.exe` 又都在沙箱黑名单里。唯一走通的路是让 explorer 代启动
`start-bot.pyw`：父进程是 explorer，既不带注入，也不在代理的 job 对象里。

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

### 回之前先看上下文

`--list` 只认识「唤醒」，闲聊根本不进 `wg_events.jsonl`——`mention_only` 在服务端就滤掉了。
只看 `--list` 等于只看谁拍了你一下，看不到前因后果：群里刚聊完的话题、刚立的人设、
刚被纠正的说法，全都接不上，回出来自然不够灵动。

```bash
# 最近 30 条（含闲聊）
.venv/Scripts/python.exe water-group/daemon/reply.py --context 30

# 只看某条之前的部分（正在回一条稍早的消息）
.venv/Scripts/python.exe water-group/daemon/reply.py --context 30 --around 1760
```

每行是 `id 时间 发送者: 正文`。`我` 表示自己说过的话，`<- 唤醒` 是触发你的那条，
`<- 回这条` 是 `--around` 指定的目标。数据直接读 `chat_inbox`——后端在决定要不要推送之前
就已经把每条消息写进去了，所以闲聊一条不落。

**别用 `--list | tail -4` 当上下文**，那只是把唤醒日志截断，看到的是「谁 @ 了我」的最近几条。

**为什么引用不走 `send_chat_reply` 的 `quote`**：`plan_chat_reply` 只接受**当前批次**里的 `target_event_id`，而群里每来一条新消息就开一个新批次。等你写好正文，触发你的那条通常已经落在旧批次里了——此时 plan 要么直接 `target_not_in_batch` 失败，要么改投到最新那条、把引用挂到别人头上（实测回 1202 时批次翻到 1203，引用会引到 1203）。所以引用改走 OneBot 原生 `reply` 消息段，用 collector 已存的 `onebot_message_id`，跨多少批次都不受影响。plan 路径保留为兜底。

### 连着几条 @ 怎么办

群里一分钟内连发三条 @ 是常态，而监听在你拿到 `WAKE` 那一刻就退出了。这个窗口里来的 @ **不会丢**，但会被「已回」误判吞掉——两件事要一起改。

**① 回完就地等，不要退出后再另外重挂**

```bash
.venv/Scripts/python.exe water-group/daemon/reply.py <id> "<正文>" --wait 240
```

`--wait` 让 `reply.py` 发完直接进等待（`listen.py --timeout N --cycles 1 --no-digest`），空窗从「一整轮 agent 思考」压到几百毫秒。末行是 `WAKE`（有人叫你）或 `NO_WORK`（这 N 秒没人说话），都算正常返回。已有监听在跑时它会跳过，不会挂两个。

> ⚠️ **`--wait` 要放后台跑**（`run_in_background`）。前台工具调用有超时上限（本机 120 秒），`--wait 180` 会被砍掉——消息其实已经发出去了，只是等待被打断，日志里只留一个 SIGTERM。stdout 已改成行缓冲，被杀时至少能看到 `SENT`。

**② 一条回复能答完，就用 `--also` 一起记账**

```bash
reply.py 1564 "@{地牢主} 有人点你" --also 1565 --also 1566
```

一条消息同时回三个人是常态（「@A 和 @B 都别急」）。只记 `target`，剩下两条下一轮会被当成没回再答一遍；而不记账又会让真正的漏网之鱼永远显示待回。`--also` 就是把「这条回复顺带回了谁」说清楚。

**③ 发送前会自动提醒**

`reply.py` 发现你回的那条之后还有没回的唤醒，会打印：

```
  NOTE 后面还有 1 条在等：1564
       一条答得完就加 --also，答不完就先回这条再接着回
```

看到这行别闷头发，先决定是并进这条还是接着回。

**以前为什么会静默吞掉**：`up.replied_after` 的兜底是「这条之后只要出现过自己的消息，就算回过了」。回 1563 的那条落在 1564 之后，于是 1564 被记成已回——`--list` 显示 `[已回]`，`watch.py` 也把它从 `fresh` 里滤掉，用户看到的就是「@ 了没反应」。现在兜底只对**账本建立之前**的老消息生效（按 `wg_replied.jsonl` 首条时间戳划线），新消息必须在账本里才算回过。

### @ 人

正文里写 `@{花名}` 就是**真正的 @**（消息段），不是文字：

```bash
.venv/Scripts/python.exe water-group/daemon/reply.py 1516 "@{班味鹅} 学废了"
# 想在正文前面单独 @，用可重复的 --at：
.venv/Scripts/python.exe water-group/daemon/reply.py 1516 "学废了" --at 班味鹅 --at 时月
# 只查这个名字对应谁，不发：
.venv/Scripts/python.exe water-group/daemon/reply.py --who 时之锁
```

**必须带花括号**：群里正文的「@时月」是普通文字，真正的 @ 是消息段。混为一谈会把随口一提变成一次点名。

解析顺序（`resolve_member`）：纯数字当 QQ → **本群名片精确** → 档案（主名/别名）精确 → 本群名片近似 → 档案近似。名片排在档案前面，因为「花名」在群里最直白的读法是名片（`时之锁-肠宝盒`、`坏乖😋`）；档案兜住改名前的老叫法和只有熟人知道的外号（`孩董` → criss、`领导` → 时月）。

**同名就报，不猜**：`--who momo` 返回「本群名片里有 3 个：momo(953099254)、momo(928627042)、momo(196361530)」，`--who 时之` 返回「本群名片近似里有 2 个：时之地牢–月之捆绑(289554547)、时之锁-肠宝盒(2057796208)」。@ 错人比漏 @ 更像一次失误，所以歧义一律降级成文字。

解析不出来**不拒发**：名字原样以 `@某某` 文字发出，消息照常送达。单字（`@{月}`）不做近似匹配，太容易撞。`@{🐟包}`（自己）也按文字发——@ 自己没有意义。

**带 @ 的消息只能走 OneBot**：`send_chat_reply` 只吃纯文本，会把 at 段静默降级成文字，所以 `reply.py` 一旦发现 at 段就强制走 OneBot；OneBot 不可用时直接 `SEND_FAILED`，不会退化成一条没有 @ 的消息。想确认真的发出去了，用 `get_msg` 回读：

```bash
# 回读应看到 [CQ:reply,id=...][CQ:at,qq=...]，只有 at 段才算数
.venv/Scripts/python.exe -c "import sys,json,requests;sys.path.insert(0,'water-group/daemon');import reply as R;b,t=R.onebot_config();print(json.dumps(requests.post(b+'/get_msg',json={'message_id':<上一步的 message_id>},headers={'Authorization':'Bearer '+t},timeout=15).json().get('data',{}).get('message'),ensure_ascii=False))"
```

**什么时候该 @**：引用**不会**通知被引用的人，只有 @ 会响。所以

- 回谁就只引用谁，不用再 @ 一遍——引用条已经摆在那儿了，再 @ 显得急。
- 要**把某人叫出来**（点名要答案、提醒他刚才说的话、转达给第三方）才 @，一条消息最多 @ 两个。
- 不 @ 全体。真要全员才 `@{all}` 之外的场景不存在，群主权限也没有。

用户对你说「@ 时月 问问他」时，那是**指令**，落成命令要写成 `@{时月}`；群里正文里别人打的「@时月」才是文字。


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

`--no-digest` 让它超时后不沉淀、只等，用于 `reply.py --wait`：积压一多，`dossier.py --digest` 要跑几十秒，会把「等 6 秒」拖成一分钟，等待就不再可预期。

**这是唯一会叫醒你的东西，而它只活一轮。** 每次 `WAKE` 回来、回完消息之后，必须立刻重新挂上——
否则群里再有人 @ 你，消息照样落进 `wg_events.jsonl`，但没有任何进程把它变成唤醒信号，
表现出来就是「发了消息没反应」。会话中断、栈重启、上下文压缩之后同样要重挂。

**更省事的做法是别退出**：`reply.py <id> "<正文>" --wait 240` 发完就地接着等，把「回复 + 重挂」合成一次调用，空窗从几十秒压到几百毫秒（见 §3「连着几条 @」）。

判断挂没挂：`up.py --status` 的「监听」一行。`未挂` 就是没人会叫醒你，先挂上再谈别的。

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
| 桌面时不时弹黑框 | 多半是 `QQ has stopped working` 崩溃框，一个实例崩一次攒一个。`SetErrorMode` 已压掉；如果还在弹，说明有别的东西在开控制台，查是谁没带 `CREATE_NO_WINDOW` |
| QQ.exe 越攒越多 | `kill_stale_instances` 匹配失效。**认实例只认 `-q <self_account>`**，别认启动器注入的开关（`--enable-logging` 已被改成 `--disable-gpu`，跟着它走会漏杀） |
| 注入实例每 20~40 秒崩一次，`0xC06D007F` | **栈是从代理里起的**。沙箱的 `tsbx.dll` 沿进程链继承到 QQ，delay-load 找不到导出就自杀。先看 `napcat-watchdog.log` 有没有 `WARN ... tsbx.dll`；有就是脏栈，`up.py --stop` 之后改用 `start-bot.pyw` 从 explorer 起 |
| 判断某个 QQ 干不干净 | `Get-Process -Id <pid> -Module` 里**不该**出现 `tsbx.dll`。用户桌面启动的 QQ 本来就没有，所以这个判据只对注入实例有意义 |
| 重启后 NapCat 离线、`--status` 却仍报「后端 在线」、启动器跑完毫无反应 | `--stop` 没停干净：残留 `app.py`/`collector.py` 占着 47860/48777，启动器按端口判幂等直接退出。按 §0「重启整栈」先清端口 |
| @ 了但群里没变蓝 | 三种：名字没解析（正文里留成了 `@某某` 文字）、那人不在本群（`--who` 会说）、或者消息走了 `send_chat_reply`（它只吃纯文本）。`get_msg` 回读一眼就分清 |

**注入实例的身份**：`--disable-gpu    -q 3885934113`。用户桌面启动的 QQ 命令行不带任何参数，所以按 `-q <账号>` 匹配永远不会误伤。杀的时候带 `/T`，只杀主进程会把 renderer 和 network utility 留在原地继续占 profile。
