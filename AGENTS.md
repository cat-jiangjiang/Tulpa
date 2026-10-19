# Tulpa AI Agent 协作与开发指南 (AGENTS.md)

欢迎使用 Tulpa 仓库。本指南是专为进入本项目的 AI Agent（以及协同开发者）设计的操作手册，旨在说明项目架构、开发规范、核心子系统设计以及强制性安全门禁。

---

## 1. 项目定位与核心架构

Tulpa 是一个**本地优先（Local-First）**的多端聊天数据汇聚、Agent 交互与 OneBot 11 桥接中间件，基于 Model Context Protocol (MCP) 标准向桌面端及外部 Agent 提供服务。

```
                    ┌────────────────────────┐
                    │   Desktop / IDE Agent   │ (Gemini, Claude, Devin, etc.)
                    └───────────┬────────────┘
                                │ MCP Protocol (stdio / SSE)
                    ┌───────────▼────────────┐
                    │     Tulpa MCP Server   │ (tools/ & chatlocal/)
                    └─────┬────────────┬─────┘
                          │            │
       ┌──────────────────▼──┐      ┌──▼─────────────────┐
       │ Local Data & VFS    │      │ OneBot 11 Gateway   │
       │ (SQLite / WAL / VFS)│      │ (HTTP / WebSocket) │
       └─────────────────────┘      └──┬─────────────────┘
                                       │
                         ┌─────────────┴─────────────┐
                         │   water-group (水群子系统) │
                         │  - bootstrap.py (一键后台) │
                         │  - memory_engine.py       │
                         │    (三源输入 / 两层存储)   │
                         │  - sync_groups.py         │
                         │  - water_group/ (导入桥接) │
                         └───────────────────────────┘
```

### 核心目录职责
- `chatlocal/`: MCP 协议对话核心引擎、批次生命周期与游标推进管理（`mcp_chat.py`、`mcp_chat_turns.py`、`mcp_chat_prompts.py`）。
- `water-group/`: QQ 群水群专属自闭环模块：
  - `bootstrap.py`: 一键后台启动器、端口屏障与自检器（支持直接探针与代理绕过）。
  - `memory_engine.py`: 群友小本本动态记忆与两层存储引擎（支持 L1 画卡、L2 持久化与 MCP 原生读写）。
  - `sync_groups.py`: OneBot 群列表与 65 位成员名片/昵称同步。
  - `config.json`（本地真实配置，受 `.gitignore` 保护）与 `config.example.json`（公开模版）。
  - `personas/`: 预置角色人格卡。
- `water_group/`: 根目录标准 Python 模块兼容桥接包，使 `import water_group.memory_engine` 或 `from water_group import bootstrap` 开箱即用，消除连字号目录导入语法错误。
- `tools/`: MCP 工具声明与实现。
- `scripts/`: 全量回归测试与发布门禁脚本库。
- `data/`: 运行时数据与 SQLite 数据库存储目录（`group_memories.sqlite3`、`mcp-access.sqlite3` 等）。

---

## 2. 强制性行为与开发规范

所有在本仓库工作的 Agent 必须严格遵守以下准则：

### 2.1 包管理器与模块导入规范
- **Python 包管理器**: 强制使用 `uv` 管理依赖与执行命令（如 `uv run python ...`），**严禁使用传统的 `pip`**。
- **Node.js 包管理器**: 强制使用 `pnpm`，**严禁使用 `npm` 或 `yarn`**。
- **模块导入规范**: 导入水群子系统模块时，必须统一使用标准 Python 命名 `import water_group...`，严禁编写 `sys.path.insert(0, ...)` 硬编码路径。

### 2.2 进程与系统安全规范
- **严禁使用宽泛通配符杀进程**：坚决禁止执行 `taskkill /F /IM node.exe` 或 `python.exe`（会连带杀死当前 Agent 宿主）。
- **保护宿主机 QQ 进程**：宿主机正在运行的官方桌面 QQ（PID 1720 / 账号 1716200584）是本地读取器与通信源，**绝对不可终止**。
- **端口清理三步走**：
  1. `netstat -ano | findstr :<port>` 查找精确 PID。
  2. `tasklist /FI "PID eq <pid>"` 验证进程属性。
  3. 确认非 Agent 与非关键系统进程后，使用 `taskkill /F /PID <pid>` 精准关闭。

### 2.3 代码架构与文件修改规范
- **单一职责（SRP）与 DRY 原则**：杜绝多功能堆砌与复制粘贴，复用逻辑必须抽象为独立函数或模块。
- **禁止 Shell 直接写文件**：严禁使用 `echo`、PowerShell `Out-File` 或重定向符（`>`、`>>`）修改/创建代码文件，必须使用专用编辑写入工具以防转义与编码丢失。
- **文件局部编辑优先**：修改现有代码时优先使用精确匹配替换，避免全文件盲目重写。

### 2.4 隐私与版本控制规范
- **严禁提交敏感文件**：`water-group/config.json`、真实人设卡（如 `water-group/personas/yu_bao.md`）、`.env` 均在 `.gitignore` 保护范围内，严禁强制添加到 git。
- **禁止批量 `git add .`**：永远使用 `git add <file>` 逐个暂存文件。
- **代码提交前门禁**：任何 commit 前必须运行并通过安全扫描：
  ```bash
  uv run python scripts/check_publication.py
  ```

---

## 3. 水群记忆引擎与 MCP 原生交互体系

### 3.1 三源数据输入 (Three-fold Input)
1. **显式调侃指令**: 实时识别群聊打闹命令并落库：
   - `@机器人 记在小本本上 [某人] 其实 [喜好/狠料]`
   - `@机器人 标注/认认/绑定 <QQ号/@提及> 是 <人名> (<角色>)`
2. **交互捎带提取 (Piggyback)**: 在直接回复或 @ 对话中，轻量提取发言者的口癖、态度与交互偏好。
3. **空闲摘要消化 (Idle Digest)**: 在轮询等待的空闲期，小批次异步分析未处理的历史消息，丰富人物档案。

### 3.2 两层存储架构 (Two-tier Storage)
- **Tier 1 (L1 Context)**: 超紧凑画像卡（约 80 tokens/人），发言时自动注入模型上下文：
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
2. **OneBot 名片同步**: 每次启动自动通过 `get_group_member_list` 获取所有成员的最新名片并推入别名表。
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

在向仓库推送或交付任何改动前，必须依次执行并通过以下测试套件：

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

# 8. 发布代码敏感信息与泄露扫描 (全量源文件审查)
uv run python scripts/check_publication.py
```

---

## 6. 快速启动指南

### 6.1 启动水群后台服务
由 Agent 或用户唤醒水群时，推荐直接使用一键自检启动脚本：
```bash
# 完整自检并启动后台守护 (带端口就绪握手屏障)
uv run python water-group/bootstrap.py

# 仅检查网络、OneBot连接与成员标注状态 (不保持运行)
uv run python water-group/bootstrap.py --status-only
```

### 6.2 启动主 MCP 服务
```bash
uv run python app.py
```
