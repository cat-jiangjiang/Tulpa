# Tulpa AI Agent 协作与开发指南 (AGENTS.md)

欢迎使用 Tulpa 仓库。本指南是专为进入本项目的 AI Agent（以及协同开发者）设计的操作手册，旨在说明项目架构、开发规范、核心子系统设计以及强制性安全门禁。

面向使用者的教程在 `doc/`（水群协议与各 MCP 工具的验证记录、安全说明、两轮 DRY/SRP 审计报告）；水群子系统的 Agent 侧规则另有 `.agents/rules/water_group.md` 与 `.agents/skills/water-group/SKILL.md`。本文件面向**仓库内的开发与维护工作**。

## 0. 接管水群（用户说「在 xxx 群挂机水群」时先看这里）

一句话入口，**必须由 explorer 代启动**（前提：`.venv/` 已存在，没有就先跑 `setup.ps1` 或 `uv sync`）：

```bash
.venv/Scripts/python.exe -c "import subprocess,pathlib; subprocess.Popen(['explorer.exe', str(pathlib.Path('water-group/start-bot.pyw').resolve())])"
```

**不要用 `uv run python water-group/bootstrap.py` 起栈。** 代理沙箱给每个子进程注入 `tsbx.dll`，注入沿 `up.py → supervisor → watchdog → NapCatWinBootMain → QQ` 一路继承，QQ 约 20 秒后必崩（`0xC06D007F`，delay-load 的 `ERROR_PROC_NOT_FOUND`），watchdog 于是无限重注入，桌面上堆满崩溃框。`start-bot.pyw` 是 `.pyw`（关联 `pythonw.exe`，无控制台），经 explorer 拉起时父进程是 explorer，整条链干净。`bootstrap.py` 现在检测到注入会直接 `exit 2` 拒绝启动。

起好之后（先读状态，再挂监听）：

```bash
.venv/Scripts/python.exe water-group/daemon/up.py --status            # 只读复查
.venv/Scripts/python.exe water-group/daemon/listen.py --timeout 300   # 挂监听（只活一轮，WAKE 后必须重挂）
.venv/Scripts/python.exe water-group/daemon/reply.py --list            # 看谁 @ 了我
.venv/Scripts/python.exe water-group/daemon/reply.py --context 30      # 回之前看群里在聊什么（含闲聊）
.venv/Scripts/python.exe water-group/daemon/reply.py <id> "<正文>"      # 回一条，自动带原生引用；发完自动开追问窗口
```

追问窗口：回复成功后 `reply.py` 自动开窗（默认再跟 10 条或静默 180 秒），群里继续聊的
几句也会唤醒监听（唤醒行带 `[追问]` 前缀），话题能接着走；`--no-followup` 单次关闭，
`config.json` 的 `followup` 段调参数，详见 `.agents/rules/water_group.md`。

日常循环、按花名 @ 人、连发 @ 的处理、排查表，全部在 **`.agents/rules/water_group.md`**，动手前读它。本文件后面的内容是仓库开发与维护规范，不是挂机 SOP。

---

## 1. 项目定位与核心架构

这是 Tulpa 的 water-group-only slim 分支：把上游的桌面产品壳（.NET 桌面壳、Web 前端、读微信、Linux 终端、安装包）整个砍掉，只留水群这一条路。后端入口是单一进程 `server_wg.py`（FastAPI），监听 OneBot 11 实时事件，通过 Model Context Protocol (MCP) 向外部 Agent 暴露工具。**Windows only**。

```
        ┌──────────────────────────────────────────────────┐
        │            外部 Agent / 桌面客户端                 │
        │   (Gemini, Claude, Codex, DeepSeek, WorkBuddy…)  │
        └───────────────┬──────────────────────────────────┘
                        │ MCP (stdio/SSE)  HTTP/WebSocket
              ┌─────────▼──────────┐  ┌──────────────────────┐
              │  chatlocal/ 引擎    │  │  OneBot 11 网关      │
              │ - store.py SQLite  │  │ (NapCat, HTTP / WS)  │
              │ - mcp_* MCP 工具层  │  │  43000 / 43001       │
              │ - prompts/mcp_chat/│  └──────────┬───────────┘
              └───────────┬───────┘             │
      ┌───────────────────┴─────────────────────┘
      │          water-group (水群子系统)              │
      │  memory_engine.py · memory_commands.py        │
      │  memory_format.py · bootstrap.py               │
      │  sync_groups.py · personas/                   │
      └───────────────────────────────────────────────┘
```

### 核心目录职责

| 目录 | 职责 |
|------|------|
| `chatlocal/` | 应用核心：`store.py`（SQLite/WAL 连接策略）、`mcp_*` MCP 工具与路由层（`mcp_server.py`、`mcp_tools.py`、`mcp_chat*.py`、`mcp_qq*.py`、`mcp_reactions.py`、`mcp_sticker_library.py` 等）、`agent*.py` 会话与人格、`prompts/mcp_chat/` 人格卡模板、`onebot*.py` 网关、`voice.py` 云端 ASR |
| `server_wg.py` | 后端唯一入口：FastAPI，HTTP `47860` / MCP `48777`，`install_mcp_routes` 挂载 MCP；不 import `app.py`/Gradio（已删） |
| `water-group/` | QQ 群水群专属自闭环模块：`bootstrap.py`（一键后台启动、端口屏障与自检）、`memory_engine.py`（群友小本本动态记忆，两层存储）、`memory_commands.py`（指令语法解析）、`memory_format.py`（L1 画像卡渲染）、`sync_groups.py`（群列表/名片同步）、`personas/` 角色人格卡 |
| `water_group/` | 根目录标准 Python 模块兼容桥接 shim（连字符目录不可直接 import）。**它是唯一知道连字符目录存在的 water-group 文件**：通过扩展自身 `__path__` 使每个文件以 `water_group.<name>` 恰好可达一次；子模块经 PEP 562 懒加载解析。**严禁把连字符目录加入 `sys.path`**——同一文件以两个名字加载会拆分状态（记忆引擎单例曾因此损坏） |
| `scripts/` | 检查与辅助脚本，以及共享基建：`fixture_server.py`（假服务器样板基类）、`check_fixture_support.py`（隔离环境/临时目录/网络护栏）、`incremental_common.py`（增量游标指纹）、`setup-napcat.ps1`（NapCat Shell 拉取器，pinned + sha256 校验） |
| `doc/` | 水群与 MCP 协议文档：`MCP_CHAT*`、`MCP_REACTIONS*`、`GROUP_MANAGEMENT`、`REPLY_TIMING`、`WATER_GROUP_ONLY_PLAN`、`SECURITY`、`THIRD_PARTY`，及两轮 DRY/SRP 审计报告 |
| `tools/` | NapCat Shell（OneBot 11 适配器，`setup-napcat.ps1` 拉取，受 `.gitignore` 保护）。上游的 qq-reader / wechat-reader / snowluma 已随桌面壳删除 |

**运行时/本地目录（均受 `.gitignore` 保护，严禁提交）**：`data/`（SQLite 库）、`imports/`、`reports/`、`build/`、`.env`、`.cache/`、`.tmp/`、`water-group/config.json`、`water-group/status.json`、`water-group/logs/`、`*yu_bao*`。

---

## 2. 强制性行为与开发规范

所有在本仓库工作的 Agent 必须严格遵守以下准则：

### 2.1 包管理器与模块导入规范
- **Python 包管理器**: 强制使用 `uv` 管理依赖与执行命令（如 `uv run python ...`），**严禁使用传统的 `pip`**。
- **Node 脚本**: 仓库无 `package.json`，`.cjs` / `.mjs` 均为零依赖直跑（`node scripts/xxx.cjs`）；**如确需引入 Node 依赖，必须使用 `pnpm`，严禁 `npm` / `yarn`**。
- **模块导入规范**: 导入水群子系统模块时，必须统一使用标准 Python 命名 `import water_group...`；**严禁**为连字符目录 `water-group/` 编写 `sys.path.insert(0, ...)`。

### 2.2 进程与系统安全规范
- **严禁使用宽泛通配符杀进程**：坚决禁止执行 `taskkill /F /IM node.exe` 或 `python.exe`（会连带杀死当前 Agent 宿主）。
- **保护宿主机 QQ 进程**：宿主机正在运行的官方桌面 QQ 是通信源，**绝对不可终止**。杀注入实例时**认 `-q <self_account>`**，别认启动器开关，更不要按进程名通配。
- **端口清理三步走**：
  1. `netstat -ano | findstr :<port>` 查找精确 PID。
  2. `tasklist /FI "PID eq <pid>"` 验证进程属性。
  3. 确认非 Agent 与非关键系统进程后，使用 `taskkill /F /PID <pid>` 精准关闭。

### 2.3 代码架构与文件修改规范
- **单一职责（SRP）与 DRY 原则**：杜绝多功能堆砌与复制粘贴，复用逻辑必须抽象为独立函数或模块。两轮 DRY/SRP 审计的方法论与既有结论见 `doc/DRY_SRP_AUDIT_2026-10-19.md` 与 `doc/DRY_SRP_AUDIT_2026-10-20.md`，动大手术前先读。
- **禁止 Shell 直接写文件**：严禁使用 `echo`、PowerShell `Out-File` 或重定向符（`>`、`>>`）修改/创建代码文件，必须使用专用编辑写入工具以防转义与编码丢失。
- **文件局部编辑优先**：修改现有代码时优先使用精确匹配替换，避免全文件盲目重写。
- **保留既有安全语义**：不得削弱数据迁移、范围校验、审批与原文来源（provenance）检查；模型不能自行批准发送或群管理操作。

### 2.4 隐私与版本控制规范
- **严禁提交敏感文件**：`.env`、`water-group/config.json`、`water-group/status.json`、真实人设卡（`*yu_bao*`）、`data/`、`imports/`、`reports/`、`tools/` 均在 `.gitignore` 保护范围内，严禁强制添加到 git。
- **禁止批量 `git add .`**：永远使用 `git add <file>` 逐个暂存文件。
- **代码提交前门禁**：任何 commit 前必须运行并通过安全扫描：
  ```bash
  uv run python scripts/check_publication.py
  ```
  扫描范围是 `git ls-files` 追踪的工作区 Markdown 文件的本地链接完整性与必需文档清单；不扫提交历史。

### 2.5 单一归属目录（新增代码的落脚点）

复制粘贴前先查下表；新代码必须收敛到既有单一归属，而不是再造副本：

| 关注点 | 唯一归属 |
|--------|----------|
| MCP wire-contract schema 片段（`session_id`/`idempotency_key`/`event_id`） | `chatlocal/mcp_schemas.py`（工厂函数返回 fresh dict，避免就地改写泄漏） |
| 聊天等待超时默认值 | `chatlocal/mcp_chat_limits.py: WAIT_TIMEOUT_SECONDS` |
| 文件 SHA-256 / MD5 | `chatlocal/artifacts.py`（`hashlib.file_digest` 流式，禁止 `read_bytes()` 全量读入） |
| 假服务器样板 | `scripts/fixture_server.py`（`FixtureHandler` + `fixture_server()` contextmanager） |
| 测试隔离环境 / 临时目录 / 网络护栏 | `scripts/check_fixture_support.py`（`isolated_env` / `temp_dir` / `network_guard`） |
| 增量导入游标指纹 | `scripts/incremental_common.py` |

注：`scripts/` 中大量存量脚本使用仓库根 `sys.path.insert` 模式（见二轮审计第 4 节"记录不修"），**新脚本必须遵循 canonical 模式**，不得再扩散手写样板。

### 2.6 证据与验证纪律（二轮审计的流程教训）
- **无证据不修复**：任何 finding 必须附带可直接粘贴的 `文件:行号` 证据才可进入修复；无证据一律视为虚构（曾有子代理报告整段虚构目录与常量）。
- **完成声明以磁盘为准**：工人/子代理报告"已完成"后，lead 必须独立用 `git status`、文件存在性、门禁原始输出复核，不得采信转述。
- **门禁安静窗口 solo 执行**：测试按序单独运行，不在并发写窗口内跑；红门禁先用 git worktree pristine HEAD 对照归因，再决定是否修复。

---

## 3. 水群记忆引擎与 MCP 原生交互体系

### 3.1 三源数据输入 (Three-fold Input)
1. **显式调侃指令**: 实时识别群聊打闹命令并落库（语法解析在 `memory_commands.py`）：
   - `@机器人 记在小本本上 [某人] 其实 [喜好/狠料]`
   - `@机器人 标注/认认/绑定 <QQ号/@提及> 是 <人名> (<角色>)`
2. **交互捎带提取 (Piggyback)**: 在直接回复或 @ 对话中，轻量提取发言者的口癖、态度与交互偏好。
3. **空闲摘要消化 (Idle Digest)**: 在轮询等待的空闲期，小批次异步分析未处理的历史消息，丰富人物档案。

### 3.2 两层存储架构 (Two-tier Storage)
- **Tier 1 (L1 Context)**: 超紧凑画像卡（约 80 tokens/人，渲染逻辑在 `memory_format.py`），发言时自动注入模型上下文：
  `【群友档案：<人名> (QQ: <uid> / 名片: <card>)】外号: ... 狠料: ...`
- **Tier 2 (L2 DB)**: SQLite 持久化（`data/group_memories.sqlite3`），包含完整时间戳事件轴、引用原话与结构化槽位。

### 3.3 MCP 原生读写工具闭环（严禁 Agent 直连写 SQLite）
Agent 必须且仅能通过标准化 MCP 工具维护群友记忆，**严禁自行编写 ad-hoc 脚本直接修改 SQLite**：
- `query_member_dossier`: 查询指定群友的小本本记忆、性格特征、别名与事件时间轴。
- `update_member_dossier`: 原生更新群友的画像字段（`key_memories`、`traits`、`aliases`、`role`），支持追加与覆盖模式（`overwrite=True`）。
- `consolidate_chat_memory`: 将一段群聊事件批量提炼、落库并同步更新相关成员的结构化档案。

### 3.4 群成员号码与名片对齐矩阵
群友频繁修改花哨的群名片（例如将名片改为梗名）会导致系统失去上下文。本系统通过三层对齐彻底解决该问题：
1. **静态配置映射**: 在 `config.json` 的 `member_mappings` 绑定 QQ 号与 `seed_key`（如 `seed:shiyue`、`seed:criss`），启动时自动合并孤儿种子。
2. **OneBot 名片同步**: 每次启动自动通过 `get_group_member_list` 获取所有成员的最新名片并推入别名表（`sync_groups.py`）。
3. **群内动态认亲**: 群友发送 `@机器人 标注 2057796208 是 孩董` 即可秒级更新映射。

---

## 4. 水群状态机与回复 SOP 规范

### 4.1 规划校验与防崩溃（Planner State Machine）
调用 `plan_chat_reply` 后，**严禁假设返回值必定包含 `plan_id`**：
1. 校验返回结果的 `state`：
   - 若 `state == "READY"`：安全获取 `plan_id = result["plan_id"]`，进入发送流程。
   - 若 `state == "REJECTED"` 且 `error_code == "stale"`：说明群内切入了目标相关的新消息。此时 `plan_id` 为 `None`，必须提取返回的 `updated_chat["input_batch"]`，使用最新批次原地重规划，切勿崩溃退出。
   - 若 `state == "SILENCE"`：表示当前语境适合潜水无需回复，流程正常结束。

### 4.2 反语癖固化机制 (Anti-Habituation)
自回归生成容易将前序回复中的单一口癖（如频繁以“……草”、“难绷”结尾）不断回喂放大。系统内置了标点清洗与多重后缀检测，当触发连续口癖时会自动在 `social_state` 提示词中注入多样性约束，Agent 应当主动响应约束、变换句式。

---

## 5. 关键验证测试套件

在向仓库推送或交付任何改动前，必须依次执行（**安静窗口单独按序运行**）并通过以下门禁。

### 5.1 水群与 MCP 聊天核心门禁
```bash
# 1. 动态记忆引擎、MCP读写工具与两层存储测试 (10项核心门禁)
uv run python scripts/check_group_memory.py

# 2. 一键后台启动健康检查与代理隔离测试
uv run python scripts/check_bootstrap_health.py

# 3. 根目录 Python 模块化兼容桥接测试
uv run python scripts/check_water_group_import.py

# 4. 反语癖固化与多样性提示词注入测试
uv run python scripts/check_anti_habituation.py

# 5. 状态机拒绝 (stale) 与重规划健全性测试
uv run python scripts/check_plan_recovery.py

# 6. MCP 聊天输出稳健性测试 (输出截断、游标与ACK校验)
uv run python scripts/check_mcp_chat_output.py

# 7. 对话轮次生命周期与原生引用测试 (19项高并发与防竞争检查)
uv run python scripts/check_mcp_chat_turns.py

# 8. 发布代码敏感信息与泄露扫描 (工作区 Markdown 链接完整性 + 必需文档清单)
uv run python scripts/check_publication.py
```

### 5.2 仓库级回归门禁
```bash
# MCP 协议层契约
uv run python scripts/check_mcp.py
```

完整 23 门禁清单与顺序见 `doc/WATER_GROUP_ONLY_PLAN.md` §8。`scripts/` 里还残留上游桌面壳的旧门禁脚本（`check_tulpa.py` / `smoke.py` / `check_desktop.py` / `check_linux_suite.py` / `check_terminal_tui.py` / `check_chat_browser*.py` 等），它们 import 已删除的 `chatlocal.tulpa` / `render` / `chat_view` / `desktop_routes`，跑起来直接 ModuleNotFoundError，不列入回归集，不在本分支维护。

**红门禁归因纪律**：失败时先用 git worktree pristine HEAD 对照——若同族失败则为既有环境问题（沙箱 WAL 竞态等，见审计报告第 3 节），记录在案，不要盲目"修复"。

---

## 6. 快速启动指南

### 6.1 启动水群后台服务

**唯一入口是 `water-group/start-bot.pyw`，必须由 explorer 代启动**（原因见 §0：沙箱注入会杀死 QQ）：

```bash
.venv/Scripts/python.exe -c "import subprocess,pathlib; subprocess.Popen(['explorer.exe', str(pathlib.Path('water-group/start-bot.pyw').resolve())])"
```

它幂等：端口已在就什么都不做；否则先 `up.py --stop` 退掉旧栈再起新的。会一直不返回，输出在 `water-group/logs/launcher.log`。

`water-group/bootstrap.py` 降级为**只读自检**。在代理沙箱里跑它会被直接拒绝（exit 2），因为从那里起的栈会把 `tsbx.dll` 一路继承到 QQ：

```bash
# 仅检查端口、OneBot 与后端健康（不保持运行、不拉起任何进程）
uv run python water-group/bootstrap.py --status-only
```

### 6.2 启动主后端（前台调试用）

常驻栈走 §6.1 的 `start-bot.pyw`（它内部拉起 `supervisor → server_wg.py + collector + db_keeper + watchdog`）。只在需要前台看日志时单独跑后端：

```bash
uv run python server_wg.py
```

`server_wg.py` 是 slim 的后端唯一入口：FastAPI，HTTP `47860` / MCP `48777`。端口被占会 exit 2。

### 6.3 停栈

```powershell
.\stop.ps1     # 只退 server_wg.py（前台调试栈）
```

常驻栈用 `up.py --stop`（带 `/T` 退掉守护/看护/采集/后端整棵树），见 `.agents/rules/water_group.md` §0「重启整栈」。

### 6.4 常用辅助
```powershell
# 拉 NapCat Shell（首次或换版本；pinned + sha256 校验）
.\scripts\setup-napcat.ps1
.\scripts\setup-napcat.ps1 -Update   # 拉最新版并打印新 pin，供人工复核后写回脚本
```
