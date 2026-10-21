# Phase 0 — 基线冻结

> 状态: **已完成** (2026-10-21)。`pre-slim` tag 已打, `slim` 分支已建, 仓库外备份完整 (integrity_check 全 ok), 23 门禁基线日志落盘 `.plan/baseline/2026-10-21/`, 红灯项登记 `02-known-red.md`。端到端基线按 §0.7 延后到 Phase 1 §1.6 冷启动验收 (已通过, 见 `logs/phase-1/01-cold-start.txt`)。
> 目标：在**不改任何代码**的前提下，冻结可回退的基线、备份不可再生的资产、把 23 个门禁的真实状态记录成对照表。
> 预计：0.5 天。前置：无。产出：`pre-slim` tag、`slim` 分支、`.plan/baseline/2026-10-21/` 全套日志。

---

## 0.1 预检（5 min）

```bash
cd <repo-root>   # 本仓库根目录
git status --short          # 期望只有 doc/WATER_GROUP_ONLY_PLAN.md 与 .plan/ 未跟踪
git rev-parse --short HEAD  # 记录：应为 ce1a575
git log --oneline -1
```

- [x] 确认没有未提交的代码改动（只有文档/计划文件可以）。
- [x] 确认 `slim` 分支不存在：`git branch --list slim`（存在则先确认归属）。
- 记录到 `baseline/2026-10-21/00-precheck.txt`。

## 0.2 停栈（10 min，必须停干净）

```bash
uv run python water-group/daemon/up.py --stop
```

**已知坑**（commit 7715eb9）：`up.py --stop` 可能留下 `app.py`/`collector.py` 占着端口。停完必须核对：

```bash
netstat -ano | findstr ":47860 :48777 :43000 :43001"
```

- 若仍有监听：用 `tasklist /FI "PID eq <pid>"` 确认进程，再 `taskkill /PID <pid> /F`。
- 再确认 supervisor/up 不在（Windows 11 默认无 `wmic`，改用 PowerShell 或 tasklist）：
  ```powershell
  powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'supervisor.py|daemon.up.py' } | Select-Object ProcessId,CommandLine"
  ```
  或 `tasklist /V /FO CSV | findstr supervisor.py`。
- [x] 43000/43001（NapCat）也停掉，避免备份时 DB 仍在写。
- 记录最终端口状态。

## 0.3 备份 gitignored 资产（15-30 min）

备份到**仓库外**，避免被 git 操作误伤：

```bash
DEST="/c/Users/VerNe/Tulpa-backup/20261021"
mkdir -p "$DEST"
cp -a .env "$DEST"/.env
cp -a water-group/config.json "$DEST"/wg-config.json
cp -a water-group/personas "$DEST"/personas
# data/ 含 voice worker.lock、mcp-chat-media/wechat-reader 的 ACL 拒绝项，cp -a 会漏；
# 用 robocopy /B (backup mode) 绕 ACL，且必须先按 §0.2 真停栈（含 voice worker）
robocopy "$(cygpath -w data)" "$(cygpath -w "$DEST")\\data" /E /B /R:2 /MT:8 /NFL /NDL /NJH /NJS
cp -a .tmp "$DEST"/tmp-state
robocopy "tools\\napcat-shell" "$(cygpath -w "$DEST")\\napcat-shell" /E /MT:8 /NFL /NDL /NJH /NJS
```

- [x] `data/` 至少包含：`mcp-access.sqlite3`、`chats.sqlite3`、`group_memories.sqlite3`、`mcp-language.sqlite3`、`mcp-chat-media/`。
- [x] 对副本做完整性检查：
  ```bash
  for db in "$DEST"/data/*.sqlite3; do uv run python -c "import sqlite3,sys;c=sqlite3.connect(sys.argv[1]);print(sys.argv[1], c.execute('PRAGMA integrity_check').fetchone()[0])" "$db"; done
  ```
- [x] 备份清单写到 `baseline/2026-10-21/01-backup-manifest.txt`（文件数 + 总大小，`du -sh`）。
- [x] `yu_bao.md` 在 `water-group/personas/` 内，确认已复制（`ls "$DEST"/personas`）。
- 说明：`.env` 与 `config.json` 含 Token/群号，**只放仓库外**，不要放进 `.plan/`。

## 0.4 提交计划文档 + 打 tag + 建分支（5 min）

```bash
git add doc/WATER_GROUP_ONLY_PLAN.md .plan
git commit -m "docs: water-group slim plan and phase playbooks"
git tag -a pre-slim -m "baseline before water-group-only slim"
git checkout -b slim
```

- 若不想把 `.plan/` 入库：先 `echo ".plan/" >> .gitignore` 再提交（但仍建议保留在磁盘，tag 不包含它）。
- [x] `git tag --list pre-slim` 有输出；`git branch --show-current` = `slim`。

## 0.5 跑基线门禁（30-60 min）

按 overview §4 的顺序**串行**执行（门禁会自起 loopback fixture，不要并行）。建议逐条跑并落日志：

```bash
OUT=".plan/baseline/2026-10-21"
gates="check_mcp_chat_output check_anti_habituation check_plan_recovery check_reply_quote check_reply_timing
check_memory_engine_imports check_water_group_import check_bootstrap_health
check_wake_ledger check_followup check_reply_context
check_group_memory
check_mcp check_mcp_onebot check_mcp_chat check_mcp_chat_targets check_mcp_chat_prompts
check_mcp_reactions check_mcp_chat_media check_mcp_chat_turns check_mcp_qq_extensions check_mcp_language_learning
check_publication"

for g in $gates; do
  echo "=== $g ===" | tee -a "$OUT/gates.log"
  uv run python "scripts/$g.py" > "$OUT/$g.log" 2>&1
  echo "$g rc=$?" | tee -a "$OUT/gates.log"
done
```

- [x] `gates.log` 里每条的 `rc` 都记录；非 0 的单独看日志判断是**真失败**还是环境问题（端口占用/缺 NapCat）。
- [x] 已知可能红的项：`check_publication`（`.plan/` 新文件含 `C:/Users/<user>` 等路径会命中 `PERSONAL_PATH` 正则，且 `.plan/` 不在 `PRIVATE_ROOTS` 白名单——执行前先把它加入白名单或剥离路径，否则基线必红）；`check_group_memory`（人格卡种子数变化会抖）。
- [x] 失败项登记到 `baseline/2026-10-21/02-known-red.md`（脚本/原因/是否阻塞后续）。
- **注意**：`check_mcp_qq_extensions` 间接依赖 Pillow（经 `mcp_chat_media.py:28 from PIL import Image`），本路线保留媒体，应能跑；若环境缺 Pillow，先 `uv sync` 再重跑。`check_mcp_language_learning` 不依赖 Pillow/opencc，无需特殊处理。opencc 全仓仅 `chatlocal/voice.py:73` 使用（voice 在 Phase 3 删除集），当前环境未装 opencc。

## 0.6 环境快照（5 min）

```bash
git rev-parse HEAD > "$OUT/03-rev.txt"
uv run python -V >> "$OUT/03-rev.txt"
uv pip freeze > "$OUT/04-pip-freeze.txt"
git ls-files | wc -l >> "$OUT/03-rev.txt"
```

- [x] 记录 `fastapi/uvicorn/mcp/httpx/websockets/Pillow/jsonschema/requests` 实际版本（`pip-freeze` 内可查）。
- [x] 记录 `data/` 各库大小与 `grants` 表当前 epoch（只读）：
  ```bash
  uv run python - <<'PY'
  import sqlite3, json
  db = sqlite3.connect("data/mcp-access.sqlite3")
  print(db.execute("select name,epoch from grants").fetchall())
  print(db.execute("select * from settings").fetchall())
  PY
  ```

## 0.7 端到端基线（留到 Phase 1 §1.6，本 Phase 不重启栈）

> Phase 0 只冻结基线（tag/分支/备份/门禁/快照已做），不重启栈、不跑真实 @。
> 重启栈 + 真实 @ 回复本质是 Phase 1 §1.6 冷启动验收的事，且**起栈必须用 `start-bot.pyw` 经 explorer 代启**（见 AGENTS.md §0/§6.1），不能用 `up.py` 起栈——代理沙箱里 `up.py → supervisor → watchdog → NapCat → QQ` 会注入 `tsbx.dll`，QQ 约 20 秒后必崩（`0xC06D007F`），watchdog 无限重注入。
> 现在重启只会把刚停干净的栈又跑起来，Phase 1 开头又要停，来回折腾。

- [x] 栈已停干净（§0.2 netstat/taskkill 核对，47860/48777/43000/43001 全无 LISTENING）。
- [x] 端到端基线（47860/48777 在线 + heartbeat 新鲜 + session id + 一次真实 @ 回复）**留到 Phase 1 §1.6 验收时做**，起栈用 `start-bot.pyw` explorer 代启。（Phase 1 §1.6 已验收通过，见 `logs/phase-1/01-cold-start.txt`。）

## 0.8 退出标准与回滚

**退出标准**
- [x] `pre-slim` tag 存在且指向含计划文档的 commit；当前分支 `slim`。
- [x] 仓库外备份完整，`integrity_check` 全 `ok`，清单落盘。
- [x] 23 条门禁全部有日志；红灯项已登记并判断是否阻塞。
- [x] 端到端基线（或明确的环境阻塞说明）已记录。（延后到 Phase 1 §1.6 完成。）

**回滚**：`git checkout <原分支>`；删 `slim` 分支与 tag；从备份目录恢复 `data/`（仅当数据被动过）。备份目录：仓库同级 `Documents/Tulpa-backup-20261021`（非 `%USERPROFILE%`，避免删备份要管理员权限）。

---

## 风险

| # | 风险 | 触发 | 后果 | 缓解 |
|---|---|---|---|---|
| P0-R1 | `up.py --stop` 留进程占端口 | 已知 bug | 备份时 DB 仍在写；Phase 1 冷启动误判 | 0.2 的 netstat/taskkill 强制核对 |
| P0-R2 | WAL 模式下直接拷 DB | 未停栈 | 备份不一致，回滚即坏 | 先停栈再拷；副本跑 `integrity_check` |
| P0-R3 | 门禁互相抢 loopback 端口/临时实例 | 并行跑 | 假红/假绿 | 串行；失败先看是否 `EADDRINUSE` |
| P0-R4 | `check_publication` 把 `.plan/` 判为泄露 | 新文件含路径/关键词 | 基线红 | 先看命中；必要时把 `.plan/` 加入扫描白名单（Phase 3 处理） |
| P0-R5 | tag 打在未提交文档之前 | 先 tag 后 commit | 基线不含计划文档 | 顺序固定：commit → tag |
| P0-R6 | NapCat 备份/重启失败 | QQ 未登录/沙箱 | 无法做端到端基线 | 记录环境阻塞，Phase 1 再验；不阻塞备份与门禁 |
| P0-R7 | 备份目录被误当仓库内容提交 | 备份放仓库内 | 敏感 Token 入 git | 备份强制放 `%USERPROFILE%` 外；`.gitignore` 不含该路径也无所谓 |
