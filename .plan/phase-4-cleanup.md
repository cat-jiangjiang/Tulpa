# Phase 4 — 收尾（可选但建议做）

> 目标：让 slim 仓库“可交付、可复现、可观察”，并把 2 周验证期的检查动作固定下来。
> 预计：0.5-1 天 + 2 周观察。前置：Phase 3 完成、23 门禁全绿。

---

## 4.1 文档重写（0.5 天）

- `README.md`：只留水群闭环——架构图（start-bot → up → supervisor → server/collector/db_keeper/watchdog）、端口（47860/48777/43000/43001）、启动/停止、配置（`water-group/config.json` + `.env` 5 键）、人格卡目录、门禁命令、Windows-only 声明。
- `AGENTS.md`：更新进程树、`.tmp` 契约、`reply.py`/`listen.py` 用法；删掉残留的 `web/`/`desktop/`/`snowluma`/Linux 终端版章节（line 46/57/74/75/127/254/259-262）。`.agents/rules/water_group.md` 与 `.agents/skills/water-group/SKILL.md` 无 Gradio/桌面章节，只需把 `app.py` 引用改成 `server_wg.py`。
- `doc/` 清理：`doc/` 实际 60+ 文件，需逐文件判断（不只点名 3 类）。明确删/迁移：`DESKTOP.md`、`IMPORTS.md`、`INVESTIGATION.md`、`LINUX_*.md`（10 个）、`MCP_STICKERS*.md`、`NAPCAT_QQ_EVALUATION.md`、`SNOWLUMA_*.md`、`DOCKER_*.md`、`FEATURES.md`；保留 `VALIDATION.md`、`SECURITY.md`；`MCP_CHAT*.md` 可合并进 README。同时纠正 `AGENTS.md` §2.4「扫描范围包括待推送的提交历史」——`check_publication.py` 实际用 `git ls-files` 只扫工作树，不扫历史。
- [ ] 全仓 grep 失效引用：`grep -rn "app.py\|Gradio\|snowluma\|web/\|desktop/" README.md AGENTS.md .agents/`。

## 4.2 安装/启动脚本复现（0.5 天）

- `setup.ps1`：`uv sync`（当前是 pip+requirements 文件，违反「强制 uv」，必须改）→ 检查 `tools/napcat-shell` → 提示复制 `water-group/config.example.json` 为 `config.json` → 生成 `.env`（含 `REPLY_ONEBOT_*` 5 键：`URL`/`TOKEN`/`WS_URL`/`WS_TOKEN`/`WS_TOKEN_MODE`）→ 删掉 `install_readers.py`/`prepare_reader_runtime.py` 调用（§3.3 已删这两个脚本）。`.env` 的 `URL`/`TOKEN` 可从 `config.json` 的 `onebot_http_url`/`onebot_token` 同步；`WS_URL`/`WS_TOKEN`/`WS_TOKEN_MODE` 在 config.json 无对应键，必须手动填（plan 不能说「全从 config.json 同步」）。`bootstrap.py --status-only` 前提是 NapCat 已跑——fresh clone 后还没下 NapCat/登 QQ，装依赖阶段不该跑这个自检，仅做 `uv sync` 后的 import smoke。
- `start.ps1` / `stop.ps1`：指向 `server_wg.py` 与 `up.py`；stop 直接匹配 `server_wg.py`（不留 `app.py` 兼容分支）。
- 新增 `scripts/setup-napcat.ps1`（或 `tools/fetch-napcat.py`）：下载指定版本 NapCat 到 `tools/napcat-shell/`，校验大小/哈希；不把 112MB 纳入 git。需锁版本 + 写明哈希算法。
- [ ] 从零验证：新目录 `git clone` + `setup.ps1` + 填 config + `start.ps1`，能起栈并回一条消息。

## 4.3 2 周观察期（canary）

每天（或每次重启后）记录到 `.plan/logs/canary.md`：

| 检查 | 命令/位置 | 通过标准 |
|---|---|---|
| 双端口 + 守护 | `uv run python water-group/daemon/up.py --status` | 47860/48777 在线，heartbeat <30s |
| 会话 | `.tmp/wg_session.txt` | 有值，`list_chat_sessions` 可查 |
| 401/重铸 | collector 日志 grep `401` | 无持续 401；偶发后自愈 |
| 回复链路 | `reply.py --list` / `wg_replied.jsonl` | 新 @ 能被回复，无重复回复 |
| DB 健康 | `PRAGMA integrity_check` | `ok`；WAL 不异常增长 |
| 门禁 | 23 条全跑（每周一次） | 全绿 |

- [ ] 观察期内**不改** `data/` 表结构、不升级 NapCat/QQ。
- [ ] 出现 P0（无法回复/频繁掉线）→ 按 Phase 回滚点回退，记录现象。

## 4.4 可选：拆新仓库（观察期后）

- `git clone` + 只保留 keep 集；`pre-slim` 原仓库留作上游参考。
- 必须随仓库带走的 gitignored 资产：`water-group/config.json`、`water-group/personas/yu_bao.md`、`data/`（或迁移脚本）、`tools/napcat-shell/`（下载脚本替代）。
- 新仓库首次提交前跑 `check_publication`，确认无私有人设/群号/Token。
- [ ] 不建议在观察期结束前拆仓；拆仓后原仓库至少保留 1 个月。

## 4.5 可选后续优化（不进本次范围）

- epoch 改常量（需评估 grant 全量失效的迁移方案）。
- token 按 grant 名分文件，解决单槽互相覆盖。
- `check_publication` 白名单适配 `.plan/`。
- 媒体链的 `image_identity`/`normalize` 依赖收敛（若要彻底去媒体，另起 plan 一次性删；本目录无 `§5.3`）。

---

## 风险

| # | 风险 | 触发 | 后果 | 缓解 |
|---|---|---|---|---|
| P4-R1 | 文档与 slim 结构漂移 | 只改代码不改文档 | 下次接手按旧文档操作 | 4.1 全仓 grep 失效引用 |
| P4-R2 | 私有人设/群号进新仓库 | 拆仓时整目录拷贝 | 隐私泄露 | `check_publication` + `.gitignore` 复核 |
| P4-R3 | 新仓库缺 gitignored 资产 | 只 clone 代码 | 启动失败/人格卡丢失 | 4.4 清单逐项核对 |
| P4-R4 | NapCat 版本漂移 | 下载脚本未锁版本 | 注入失败 | 脚本固定版本 + 哈希 |
| P4-R5 | 观察期改动引入新问题 | 手痒改代码 | 无法区分回归来源 | 观察期冻结代码，只记录 |
| P4-R6 | 过早删除 `pre-slim` 分支/tag | 拆仓后清理 | 失去回滚 | 保留至少 1 个月 |

**回滚**：Phase 4 只动文档/脚本，`git revert` 即可；新仓库问题则回退到原仓库继续。
