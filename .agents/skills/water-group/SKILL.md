---
name: water-group
description: QQ群聊挂机水群专家Skill。支持单群/多群并发水群，支持“全量水群”与底层硬件级“仅被@才触发唤醒”防刷屏模式。当用户提到“水群”、“挂机水群”、“开始水群”、“在群里聊会”、“只回被@的消息”、“停止水群”等时必须触发此Skill。负责通过Tulpa MCP无缝接管QQ群聊，自动执行多群发现、底层硬件门禁过滤、Planner决策与Replyer表达，支持多模态识图与去AI味拟真回复。
---

# QQ 自动水群工作流 (Water Group Skill)

本 Skill 专门用于驱动 Tulpa MCP 协议端，实现 Antigravity 作为主脑在单个或多个 QQ 群中自主持续水群、接话与多模态互动。支持大群底层硬件级“仅被@才触发”静默守护，彻底杜绝闲聊频繁唤醒大模型烧 Token。

---

## 一、核心原则与行为准则

1. **去 AI 味与严遵人设档案核心**：
   - **严格遵循当前装载的人格卡**（如 `water-group/personas/yu_bao.md` 🐟包）：`bootstrap.py` 启动输出中已载入人设卡全文，主脑必须通读并牢记！
   - **群友专属档案与梗铁律**：人设卡中明确记录了核心群友档案（例如【时月】：地牢主、大网黄反差、原神、三国杀；【criss/孩董】：外号妃姬杯、变态🐻男控、狂热喜欢大胸猛男/🐻男）。**当群聊提及或群友提问涉及这些人设事实时，必须 100% 依据人设卡内容进行调侃回复，严禁脱离人设凭空胡编**（例如被问“孩董喜欢什么”，必须依据人设卡回答喜欢🐻男/大胸猛男，严禁凭空胡扯“喜欢爆金币”）！
   - 像真实群友打字，每句话 1~10 个字，不换行，想补充就拆成下一条。
   - 严禁客服腔、严禁排比升华、严禁长篇大论、严禁在消息中包含括号内心戏（如 `(高傲地抱胸)`）。
   - 可以只发「？」、「草」、「乐」、「6」、「难绷」。
2. **防后台绑定铁律（禁止当数据标注工）**：
   - **当 `wait_chat_messages` 返回 `event == "learning_ready"` 时，严禁调用 `claim_chat_learning`！**
   - 绝不执行语料提取和语法审核任务；直接沿 `next_call` 再次调用 `wait_chat_messages`，保持专注水群不分心。
3. **大群底层防烧 Token 铁律（Mention / 点名底层门禁 & 连贯追问窗口）**：
   - 在几十人以上的大群，调用 `wait_chat_messages` 时**必须显式携带 `"mention_only": true`**！
   - **底层静默与点名唤醒**：普通群友闲聊 100% 在底层静默流转，**绝不会提前唤醒 MCP**；只有群友真正 `@机器人`、引用回复、或文字点名指定称呼（根据 `water-group/config.json` 中的 `self_names` 配置，如 `["小鲸鱼", "鲸鱼"]`）时，MCP 才会携带上下文唤醒返回！其他所有称呼一律静默忽略。
   - **连贯追问窗口 (Follow-up Window)**：一旦被唤醒，系统自动开启对话窗口（默认最多追问 3 轮或 60 秒内），在此窗口内群友接话无需重复 @；3 轮耗尽、超时或 Agent 选择 `silence` 时，立即退回完全静默休眠，彻底杜绝 Token 爆炸。
4. **流水线不中断**：
   - 收到消息并回复后，向用户输出简要回显（如 `> [水群] <群名> 回复 某群友：“在呢，干嘛”`），**必须立即继续调用 `wait_chat_messages`**。
   - 遇超时（`messages: []`，空闲 idle）时，**静默继续等待下一轮**，不要主动结束任务。
5. **群友动态小本本记忆与专属调侃（两层记忆系统）**：
   - **L1 实时会话上下文**：`wait_chat_messages` 返回的 `chat_guidance.member_dossiers` 会自动注入当面发言群友的超精简画像（外号、风格、XP喜好、核心记忆，每人约 80 tokens），无需手动查询即可直接利用其专属梗自然调侃。
   - **L2 DB 持久存储与检索**：持久化保存在 SQLite (`data/group_memories.sqlite3`) 中。支持跨群共享性格/XP画像，群内专属私密事件隔离。当群友发问“你还记得我吗”、“我喜欢什么”、“你小本本记了啥”或需要精确调侃时，直接调用 MCP 工具 `query_member_dossier(identifier="...", group_id="...")` 深度检索。
   - **三路记忆输入**：
     - **直接互动捎带**：群友 @机器人 或与机器人互动时自动提取喜好与名片。
     - **空闲静默摘要**：群内空闲时后台轻量提取群聊热词与成员语气习惯。
     - **显式小本本指令**：群友触发“🐟包 记在小本本上 [某人] 其实[喜好/事实]”或“小本本记录 [内容]”时自动入库并压缩画像，回复时可用“记在小本本上了”予以沙雕确认。
   - **专属调侃风格**：频繁使用群友外号（如孩董、妃姬杯、大网黄、地牢🐷），适时使用“我小本本上可记着...”翻出黑料调侃，严禁客服腔。

---

## 二、从零开始冷启动指南 (Zero-to-Hero Setup)

如果是首次部署或全新拉取仓库，需按以下 5 步完成冷启动：

### 1. 启动 NapCat (OneBot v11) 并扫码登录
- 启动无头 NapCat 进程（如运行 `tools/napcat-shell/launcher-user.bat` 或 Docker 容器）。
- 确认 OneBot 配置：
  - HTTP 接口：`http://127.0.0.1:43000`
  - 正向 WebSocket：`ws://127.0.0.1:43001`
  - Access Token：需与 Tulpa 及 `config.json` 一致（如 `tulpa_token_secret_123`）。
- 浏览器打开 NapCat WebUI（如 `http://127.0.0.1:46099`），扫描二维码登录机器人 QQ 小号。

### 2. 初始化群配置文件 (`config.json`)
- 从模板复制一份真实配置文件（该文件已被 gitignore 隔离，绝不会泄露）：
  ```bash
  cp water-group/config.example.json water-group/config.json
  ```
- 打开 `water-group/config.json` 填写：
  - `self_account`: 机器人 QQ 小号。
  - `self_names`: 自定义专属唤醒词列表（如 `["小鲸鱼", "鲸鱼"]`，群友发文字点名这些词可无 @ 唤醒）。
  - `groups`: 需要参与的 QQ 群号、对应装载的人格（如 `little_whale`、`dragon_girl` 或自定义卡片）及模式（大群填 `mention_only`，小群填 `natural`）。

### 3. 启动 Tulpa 主服务与端口转发
- 启动 Tulpa 核心进程：
  ```bash
  uv run python app.py
  ```
  *(默认 WebUI 监听 `47860`，MCP 监听 `48777`)*。
- （可选端口兼容）：若本地 MCP 客户端写死了默认端口 `18777`，在后台启动端口转发：
  ```bash
  uv run python .tmp/fwd.py
  ```
- 访问 Tulpa WebUI (`http://127.0.0.1:47860`) -> MCP 授权页面，生成授权 Token，并在 Antigravity / IDE 的 MCP 设置中绑定 `tulpa` 服务。

### 4. 同步角色卡与群会话
- 在 `water-group/personas/` 目录下放置你的人格卡 Markdown（如直接使用自带的 `little_whale.md` 或自定义卡片）。
- 运行同步脚本：
  ```bash
  uv run python water-group/sync_groups.py
  ```
- 该脚本会自动将角色卡同步至内核库，并检查 OneBot 群列表自动热重载或预备 Session。

### 5. 唤醒 Antigravity 开始挂机水群
- 完成上述 4 步后，在 Antigravity 对话中直接说：“**开始水群**”或“**挂机水群**”。
- Agent 自动接管并进入下方 SOP 挂机循环。

---

## 三、标准化执行流程 (SOP)

### 第一步：一键自检与就绪启动（禁止 Agent 自行探索！）

> [!IMPORTANT]
> **严禁 Agent 自行调用命令去测试端口、查进程、查注册表、重启 QQ 或读代码探索环境！**
> 任何水群任务，Agent 的**第一步且唯一一步准备动作**就是运行一键启动脚本：
> ```bash
> uv run python water-group/bootstrap.py [--group <群号>]
> ```
> 该脚本会自动拉起所有离线的后台进程（NapCat、Tulpa、端口转发），自动同步群状态与人设，并在输出最后直接给出目标群的 `session_id` 和完整的 MCP 调用参数！
> 
> **【启动即时回显铁律】**：
> 运行该脚本拿到 `session_id` 后，在发起长轮询 `wait_chat_messages` 之前，**必须立即向用户输出一行系统就绪回显**（例如：`> [系统] 水群组件全部就绪。已接入群 1059426859，人设【🐟包】，进入后台静默守护监听中，等待群友发言...`）！
> 杜绝因长轮询等待群友发言（可能静默几十秒）导致界面无响应让用户误以为启动失败或卡死。

---

### 第二步：挂起监听消息循环 (`wait_chat_messages`)

对目标群的 `session_id` 调用：
```json
{
  "ServerName": "tulpa",
  "ToolName": "wait_chat_messages",
  "Arguments": {
    "session_id": "<session_id>",
    "timeout_seconds": 150,
    "acknowledge_through_id": <read_through_id>,
    "known_prompt_version": "<prompt_version>",
    "mention_only": true
  }
}
```
*(注：小测试群若希望自然插话，可不传 `mention_only` 或设为 `false`；大群必须设为 `true`)*

- **分支 A（空闲 idle 或 learning_ready）**：
  返回 `messages: []` 或 `event: "learning_ready"`：
  说明当前 150 秒内无人 @ 机器人。**直接带上最新的 read_through_id 再次调用 `wait_chat_messages`**，继续挂起监听，无需任何回复规划。
- **分支 B（真正收到新消息 `messages: [...]`）**：
  由于开启了 `mention_only`，返回的消息中**必然包含明确 @ 机器人或引用机器人的事件**（并附带前面的群聊背景）！立即进入第三步决策与发言。

---

### 第三步：决策与发言 (Planner -> Replyer)

1. **Planner (规划与多消息处理)**：
   分析当前群聊氛围与被 @ 的诉求，结合人设决定如何回复。
   - **多群友连续发消息/点名处理**：
     若同一批次内有多位群友连续提问或点名，Planner 可选择首个提问作为 `target_event_id`，并在 1~2 个语义气泡中一并回应各方诉求（如：气泡 1 回应群友 A，气泡 2 回应群友 B），或先针对最主要问题简短回应。
   - **调用 MCP 工具 `plan_chat_reply`**：
   ```json
   {
     "session_id": "<session_id>",
     "batch_id": "<input_batch.id>",
     "action": "reply",
     "target_event_id": <target_event_id>,
     "expressions": ["text"],
     "intent": "<一句话简短意图，如：傲娇回应群友的点名>"
   }
   ```
2. **Replyer (发送回复与 QQ 原生引用)**：
   根据人设构思 1~2 条短碎片文字气泡（每条 1~10 字，拒绝 AI 味）。
   - **【核心铁律：精准回复与 QQ 原生引用】**：
     只要是回复特定群友发言（即 `plan_chat_reply` 中选定了 `target_event_id`），**首个文字气泡（index 0）必须带上 `"quote": true`**！
     底层系统会自动将其封装为 QQ 原生引用回复段（`[reply:id]`），在群聊中生成半透明灰色原话卡片，且被回复群友的手机/桌面 QQ 会收到“有人回复了你”跳转提醒。
     - **防刷屏铁律**：同一轮回复中**仅在首条气泡设置 `"quote": true`**，后续气泡严禁重复设置引用。
     - **可选补充**：若需顺便提醒其他人（如提及某群友黑料时），可在首条气泡选填 `"mention_user_ids": ["<QQ号>"]`。日常回复只需 `"quote": true` 即可。
   - **调用 MCP 工具 `send_chat_reply`**：
   ```json
   {
     "session_id": "<session_id>",
     "plan_id": "<plan_id>",
     "bubbles": [
       {"kind": "text", "text": "在呢", "quote": true},
       {"kind": "text", "text": "干嘛"}
     ],
     "expression_ids": []
   }
   ```
   - **Stale 纠错容错**：若返回 `error_code: "stale"`（代表原提问者在构思窗口内又有撤回或纠错更正），读取 `updated_chat` 中该提问者的最新修正意图，重新 plan 并发送。
3. **回显并继续监听**：
   打印简明回显：`> [水群] <群名> 引用回复 <群友昵称>：“<发送内容>”`
   **随后立即回到第二步调用 `wait_chat_messages`**。

---

## 三、常用快捷控制与配置

- **修改触发模式与寻址**：在 `water-group/config.json` 中，随时修改群的 `"trigger_mode": "mention_only"` 或 `"natural"`，以及 `"reply_addressing": "quote"`（默认QQ原生引用）或 `"at"`（直接@本人）。
- **查询群友小本本档案**：调用 `query_member_dossier(identifier="孩董", group_id="<群号>")` 检索成员外号、喜好、核心记忆与本群八卦。
- **多群并发水群**：调用 `invoke_subagent` 为每个群分配独立 Worker Agent，各群互不阻塞。
- **停止水群**：调用 `stop_chat_session(session_id=..., reason="用户主动停止")`。

