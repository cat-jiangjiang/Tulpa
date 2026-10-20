# Tulpa AI Agent 协作与开发指南 (AGENTS.md)

欢迎使用 Tulpa 仓库。本指南是专为进入本项目的 AI Agent（以及协同开发者）设计的操作手册，旨在说明项目架构、开发规范、核心子系统设计以及强制性安全门禁。

面向使用者的教程在 `doc/`（`CONTRIBUTING.md`、`VALIDATION.md`、`SECURITY.md`、`AGENT_SETUP.md` 等）；水群子系统的 Agent 侧规则另有 `.agents/rules/water_group.md` 与 `.agent/skills/water-group/`。本文件面向**仓库内的开发与维护工作**。

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

Tulpa 是一个**本地优先（Local-First）**的多端聊天数据汇聚、Agent 交互与 OneBot 11 桥接中间件，基于 Model Context Protocol (MCP) 标准向桌面端及外部 Agent 提供服务。产品形态包括：Windows 完整版（.NET 桌面壳 + 内置 Web 服务）、MCP 轻量版（仅 MCP Server）、Linux 终端源码版（CLI/TUI + 后台服务，`./tulpa` 启动）。

```
        ┌──────────────────────────────────────────────────┐
        │            外部 Agent / 桌面客户端                 │
        │   (Gemini, Claude, Codex, DeepSeek, WorkBuddy…)  │
        └───────────────┬──────────────────┬───────────────┘
                        │ MCP (stdio/SSE)  │ HTTP/WebSocket
              ┌─────────▼──────────┐  ┌─────▼──────────────┐
              │  chatlocal/ 引擎    │  │  web/ 前端（无构建） │
              │ - store.py SQLite  │  │ - platform-label.js │
              │ - mcp_* MCP 工具层  │  └────────────────────┘
              │ - agent* 会话与人格 │
              │ - tui/ Textual 界面 │
              │ - prompts/mcp_chat/│
              └───┬───────────┬────┘
                  │           │
      ┌───────────▼──┐   ┌────▼───────────────────┐
      │ OneBot 11 网关│   │ tools/ 只读读取器        │
      │ (HTTP / WS)  │   │ qq-reader/wechat-reader  │
      └───────┬──────┘   │ napcat-shell/snowluma    │
              │          │ （vendored，gitignored） │
      ┌───────▼──────────▼───────────────────────────┐
      │          water-group (水群子系统)              │
      │  memory_engine.py · memory_commands.py        │
      │  memory_format.py · bootstrap.py              │
      │  sync_groups.py · sync_personas.py · fwd.py   │
      └───────────────────────────────────────────────┘
```

### 核心目录职责

| 目录 | 职责 |
|------|------|
| `chatlocal/` | 应用核心（约 136 个模块）：`store.py`（SQLite/WAL 连接策略）、`mcp_*` MCP 工具与路由层（`mcp_server.py`、`mcp_tools.py`、`mcp_chat*.py`、`mcp_qq*.py`、`mcp_reactions.py`、`mcp_sticker_library.py` 等）、`agent*.py` 会话与人格、`tui/` Textual 终端界面、`prompts/mcp_chat/` 人格卡模板、`onebot*.py` 网关、`linux_*.py` Linux 支持 |
| `water-group/` | QQ 群水群专属自闭环模块：`bootstrap.py`（一键后台启动、端口屏障与自检）、`memory_engine.py`（群友小本本动态记忆，两层存储）、`memory_commands.py`（指令语法解析）、`memory_format.py`（L1 画像卡渲染）、`sync_groups.py` / `sync_personas.py`（群列表/名片/人设同步）、`fwd.py`（转发）、`personas/` 角色人格卡 |
| `water_group/` | 根目录标准 Python 模块兼容桥接 shim（连字符目录不可直接 import）。**它是唯一知道连字符目录存在的 water-group 文件**：通过扩展自身 `__path__` 使每个文件以 `water_group.<name>` 恰好可达一次；子模块经 PEP 562 懒加载解析。**严禁把连字符目录加入 `sys.path`**——同一文件以两个名字加载会拆分状态（记忆引擎单例曾因此损坏） |
| `web/` | 前端（26 个文件，**无独立构建步骤**）：`app.js`、`chat.js`、`mcp.js`、`platform-label.js` 等；交互修改必须用真实浏览器验证（DOM 夹具只验证脚本行为） |
| `desktop/` | 桌面壳：`ChatWeave.cs`（.NET Framework 4.8 宿主）与 `build.py`、`serve.py`、`diagnose.py`、`fetch_runtime.py`、`build_live_lock.py` |
| `harness/` | Node 侧工具（`chat-tools.mjs`），零依赖直接用 `node` 运行 |
| `tools/` | **第三方 vendored 只读读取器**（qq-reader、wechat-reader、napcat-shell、snowluma、reader-manifest.json），受 `.gitignore` 保护。**禁止直接修改副本**；读取兼容修改一律通过 `scripts/reader_patches.py` 或本项目适配层实现 |
| `scripts/` | 170+ 检查与辅助脚本，以及共享基建：`fixture_server.py`（假服务器样板基类）、`check_fixture_support.py`（隔离环境/临时目录/网络护栏）、`incremental_common.py`（增量游标指纹）、`reader_patches.py`（读取器补丁） |
| `doc/` | 50+ 篇文档：教程（`CONTRIBUTING.md`、`DESKTOP.md`、`MCP.md`、`IMPORTS.md`、`UPGRADE.md`）、验证记录（`VALIDATION.md`）、安全（`SECURITY.md`）与两轮 DRY/SRP 审计报告（`DRY_SRP_AUDIT_2026-10-19.md`、`DRY_SRP_AUDIT_2026-10-20.md`） |
| `requirements/` | 分类依赖（readers / MCP 轻量版 / 图片 / 文件解析 / 语音），根 `requirements.txt` 为基础依赖；`.venv/` 不入库 |

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
- **保护宿主机 QQ 进程**：宿主机正在运行的官方桌面 QQ（PID 1720 / 账号 1716200584）是本地读取器与通信源，**绝对不可终止**。
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
  扫描范围包括待推送的提交历史，不能只检查当前工作区文件。

### 2.5 单一归属目录（新增代码的落脚点）

复制粘贴前先查下表；新代码必须收敛到既有单一归属，而不是再造副本：

| 关注点 | 唯一归属 |
|--------|----------|
| MCP wire-contract schema 片段（`session_id`/`idempotency_key`/`event_id`） | `chatlocal/mcp_schemas.py`（工厂函数返回 fresh dict，避免就地改写泄漏） |
| 聊天等待超时默认值 | `chatlocal/mcp_chat_limits.py: WAIT_TIMEOUT_SECONDS` |
| 文件 SHA-256 | `chatlocal/file_hash.py`（`hashlib.file_digest` 流式，禁止 `read_bytes()` 全量读入） |
| Web 平台标签 | `web/platform-label.js`（两个 HTML 的首个 script） |
| 假服务器样板 | `scripts/fixture_server.py`（`FixtureHandler` + `fixture_server()` contextmanager） |
| 测试隔离环境 / 临时目录 / 网络护栏 | `scripts/check_fixture_support.py`（`isolated_env` / `temp_dir` / `network_guard`） |
| 增量导入游标指纹 | `scripts/incremental_common.py` |
| 读取器兼容补丁 | `scripts/reader_patches.py` |

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
2. **OneBot 名片同步**: 每次启动自动通过 `get_group_member_list` 获取所有成员的最新名片并推入别名表（`sync_groups.py` / `sync_personas.py`）。
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

# 8. 发布代码敏感信息与泄露扫描 (全量源文件+提交历史审查)
uv run python scripts/check_publication.py
```

### 5.2 仓库级回归门禁
```bash
# 核心行为/范围/开关/来源回归（合成夹具，不碰真实数据库）
uv run python scripts/check_tulpa.py

# 关键路径冒烟（显式合成数据）
uv run python scripts/smoke.py

# MCP 协议层契约
uv run python scripts/check_mcp.py
```

按改动范围选用对应门禁（前端：`check_chat_browser.py` / `check_chat_browser_ui.cjs`；桌面：`check_desktop.py`；Linux：`check_linux_suite.py`；终端 TUI：`check_terminal_tui.py` 等，完整清单见 `scripts/` 与 `doc/VALIDATION.md`）。

**红门禁归因纪律**：失败时先用 git worktree pristine HEAD 对照——若同族失败则为既有环境问题（沙箱 WAL 竞态、缺 Linux 安装器等，见审计报告第 3 节），记录在案，不要盲目"修复"。

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
# 仅检查网络、OneBot 连接与成员标注状态（不保持运行、不拉起任何进程）
uv run python water-group/bootstrap.py --status-only
```

### 6.2 启动主 MCP 服务
```bash
uv run python app.py
```

### 6.3 Windows 完整版 / Linux 终端版
```powershell
# Windows: 首次自动安装依赖与读取模块，然后启动（浏览器入口 http://127.0.0.1:7860/?desktop=1）
.\setup.ps1
.\start.ps1
.\stop.ps1

# Linux: 自动装依赖、起后台并打开 TUI（q 退出界面，后台继续；./tulpa stop 停止）
./tulpa
```

### 6.4 常用辅助
```powershell
# 手动刷新数据
scripts\refresh-data.ps1

# 本地 OneBot 回复发送器启停
scripts\start-reply-sender.ps1
scripts\stop-reply-sender.ps1
```
