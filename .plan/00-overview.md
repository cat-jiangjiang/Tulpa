# Water-Group-Only 改造 · 执行总览

> 进度: **Phase 0-2 完成** (2026-10-21), Phase 3 待启动。Phase 0-2 commit 链见 git log pre-slim..HEAD。
> 依据 `doc/WATER_GROUP_ONLY_PLAN.md`（2026-10-21 核对版）+ 4 个并行 subagent 复核结论（见 `01-review.md`）。
> **执行以本目录的 phase 文件为准**；主文档中被复核推翻的段落已在 `01-review.md` §3 列为勘误。

---

## 1. 路线与决策（已确认）

| # | 决策 | 结论 |
|---|---|---|
| D1 | 媒体能力（图片/表情包/反应/边聊边学） | **保留**；`media.py`/`image_identity.py`/`normalize.py` 随之保留（复核新增约束） |
| D2 | Token 供应链 | **保留现有 mint 链路**：`water-group/daemon/mint_grant.py` + `water-group/daemon/mcp_client.py` 401 重铸（`:111`）；server 只在无 grant 时补建 |
| D3 | 仓库路线 | **slim 分支先行**，Phase 0-3 在分支完成，验证 2 周后再议新仓库 |
| D4 | `water_group/` shim | **保留**（`mcp_chat.py` 反向依赖 `water_group.memory_engine`） |
| D5 | `chatlocal` 重命名 | **不做** |
| D6 | `tools/napcat-shell` | 保持 gitignored，Phase 4 补下载/安装脚本 |
| D7 | 人格卡目录切换 | **Phase 3**，与 `sync_personas` 删除、`sync_groups` 改写绑定 |
| D8 | voice 转写 ASR | **保留 voice 能力，云端化**：Phase 2 §2.7 把 ASR 从本地 `whisper.cpp` 换成 Cloudflare Worker（`https://whisper.yuuverne.site`，turbo）；删 `voice_process`/`voice_routes`/`platform_process`/`platform_lock`/`tools/whispercpp`，保留 `voice.py`/`voice_metadata.py`/`scripts/voice_source.py`+`pysilk`+`opencc`。不挂 TTS。 |

## 2. 文件索引

| 文件 | 内容 |
|---|---|
| `00-overview.md` | 本文件：决策、阶段依赖、全局约定、退出标准 |
| `01-review.md` | 4 份复核结论 + 对主文档的勘误 + 新增风险 |
| `phase-0-baseline.md` | 停栈备份、打 tag、基线门禁、基线快照 |
| `phase-1-server-entry.md` | `server_wg.py`、supervisor/bootstrap/ps1 换入口、日志与端口自检 |
| `phase-2-hubs.md` | 低耦合修复、WGStore、WGTools、mcp_access、mcp_routes、voice 云端化（§2.7） |
| `phase-3-delete-shell.md` | 删除产品壳、scripts 裁剪、人格卡切换、依赖精简 |
| `phase-4-cleanup.md` | 文档重写、安装脚本、2 周观察、可选新仓库 |
| `baseline/2026-10-21/` | Phase 0 门禁日志与快照 |
| `logs/` | 各 Phase 执行日志（可选） |

## 3. 阶段依赖

```
Phase 0 基线冻结
   └─ Phase 1 server_wg.py 换入口（非破坏，可回退）
        └─ Phase 2 三个枢纽（核心风险区）+ §2.7 voice 云端化
             └─ Phase 3 删除产品壳（机械但量大）
                  └─ Phase 4 收尾（可选）
```

- **Phase 1 之前不改任何 Python 逻辑**，只加新入口和指向。
- **Phase 2 每个子步骤都独立 commit + 独立门禁**，任一子步骤红灯只回退该 commit。
- **Phase 3 前必须重算 import 闭包**（Phase 2 已改写 mcp_tools/store/mcp_access，旧清单会失真）。

## 4. 全局约定

1. **门禁顺序**（先便宜后昂贵，避免环境问题误判）：
   1) 纯函数：`check_mcp_chat_output` → `check_anti_habituation` → `check_plan_recovery` → `check_reply_quote` → `check_reply_timing`
   2) 结构：`check_memory_engine_imports` → `check_water_group_import` → `check_bootstrap_health`
   3) daemon 契约：`check_wake_ledger` → `check_followup` → `check_reply_context`
   4) 记忆：`check_group_memory`
   5) 协议（慢）：`check_mcp` → `check_mcp_onebot` → `check_mcp_chat` → `check_mcp_chat_targets` → `check_mcp_chat_prompts` → `check_mcp_reactions` → `check_mcp_chat_media` → `check_mcp_chat_turns` → `check_mcp_qq_extensions` → `check_mcp_language_learning`
   6) 发布：`check_publication`
   共 **23 个门禁**；另有 4 个刚需非门禁脚本（`fixture_server.py`、`check_fixture_support.py`、`chat_adapter_fixture.py`、`reply_timing_report.py`）。
2. **数据零改动**：Phase 0-3 不改表结构、不改 DB 文件名、不 VACUUM；`data/` 只在备份/恢复时触碰。
3. **敏感资产**：`.env`、`water-group/config.json`、`water-group/personas/yu_bao.md`、`data/` 不进 git；`.plan/` 文档不写真实 QQ 号/群号/人名。
4. **日志**：每 Phase 的门禁输出写到 `.plan/logs/<phase>/<script>.log`，失败时保留现场。
5. **提交粒度**：Phase 2 每个子步骤一个 commit；Phase 3 每批删除一个 commit；提交信息前缀 `slim(pN): ...`。

## 5. 退出标准汇总

| Phase | 退出标准（全绿才算完成） | 状态 |
|---|---|---|
| 0 | `pre-slim` tag + `slim` 分支；备份完整性核对通过；23 门禁基线记录完毕（红灯项须登记） | 完成 |
| 1 | 冷启动后 47860/48777 双端口在线；`settings.enabled=1, port=48777`；collector 建会话；一次 @ 回复成功；杀 `app` 子进程 15s 内自愈；`server.log` 有启动日志 | 完成 |
| 2 | 23 门禁全绿；端到端 @ + 引用回复；`data/` 表结构与 Phase 0 备份一致；无新增 401 日志；voice 云端化：`from chatlocal.voice import config` 输出 `cf-whisper` 指纹，一条真实语音转写落库 `backend='cloudflare'` | 完成 |
| 3 | `check_water_group_import`/`check_bootstrap_health`/`check_publication` 全绿；`uv run python server_wg.py` 可启动；`git status` 无意外；`list_chat_personas` 正常 | 待做 |
| 4 | README/AGENTS/rules 与 slim 结构一致；`setup.ps1` 从零可跑；2 周观察无 P0/P1 故障 | 待做 |

## 6. 总回滚

- Phase 0：`git checkout main`（或原分支），删除 `slim` 分支。
- Phase 1：`git revert <commit>`；`supervisor.py` 指回 `app.py`。
- Phase 2：按 commit 逐个 `git revert`；数据未动，无需恢复。
- Phase 3：`git checkout pre-slim -- <path>` 或整树恢复 + `data/` 备份覆盖。
- Phase 4：文档回退即可，无代码影响。
