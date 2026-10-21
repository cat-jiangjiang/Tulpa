# Phase 3 — 删除产品壳

> 状态: **已完成** (2026-10-21)。7 个 `slim(p3)` commit (8265cc0/61c3aee/6b617ff/368dc1c/361585f/63b00ae/6269546)。§3.1 闭包重算落盘 `.plan/logs/phase-3/section-3-1-closure-recompute.md` (读工具引擎闭包纠正: agent_tools/artifact_tools/investigation_tools/workspace_tools/qq_tools/collections/investigation_sql/artifact_metadata 从删除集移入 keep; qq_labels 在 6269546 restore)。§3.7 23 门禁 21 绿 + 2 基线既有红 (check_mcp_chat_prompts packet>12000 / check_mcp_reactions 3s 超时,均 Phase 0.5 预注册非 slim 引入,见 `.plan/baseline/2026-10-21/02-known-red.md`); gate 日志在 `.plan/logs/phase-3/section-8/`。数据不变量守住 (integrity_check 全 ok; epoch 893b004c9ae04f16a389fa562a0b0c79 不变; settings (1,1,48777); grants 6 行; messages 323 行)。e2e (真实 @ + 引用 + 追问 + 自愈) 沿用 Phase 1 §1.6 + Phase 2 §2.6 已验证结论,Phase 3 仅删产品壳未改聊天闭环路径。
> 目标：按冻结清单删除产品壳与无关模块，保留水群闭环 + 媒体链；同步切人格卡目录、精简依赖、修正门禁。
> 预计：1.5-2 天（164 个 scripts + 136 个 chatlocal 模块，机械但量大）。
> 前置：Phase 2 完成、23 门禁全绿、端到端可用。**本 Phase 开始产生不可逆删除，但全程有 git。**

---

## 3.1 重算闭包、冻结删除清单（0.5 天）

Phase 2 已改写 `mcp_tools`/`store`/`mcp_access`，旧清单会失真，必须重算：

```bash
# 1) 列出 chatlocal 全部模块
git ls-files chatlocal/*.py | sed 's#chatlocal/##;s#\.py$##' | sort

# 2) 对 keep 模块做一层 import 扫描（含函数内 lazy）
uv run python - <<'PY'
import ast, pathlib
keep = set("""__init__ config mcp_access mcp_actions mcp_chat mcp_chat_turns mcp_chat_details
mcp_chat_hints mcp_chat_limits mcp_chat_materials mcp_chat_output mcp_chat_prompts
mcp_qq_catalog mcp_qq_permissions mcp_routes mcp_schemas mcp_server mcp_tools
message_sender onebot onebot_events onebot_segments reply_timing sqlite_util store version
retrieval media image_identity normalize
mcp_chat_local_media mcp_chat_media mcp_qq mcp_qq_media mcp_reactions
mcp_sticker_library mcp_language_learning mcp_language_store
mcp_expression_evidence mcp_jargon_learning mcp_jargon_match""".split())
root = pathlib.Path("chatlocal")
refs = {}
for name in sorted(keep):
    p = root / f"{name}.py"
    if not p.exists():
        print("MISSING", p); continue
    tree = ast.parse(p.read_text("utf-8"))
    deps = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 1:
            deps.add(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("chatlocal."):
            deps.add(node.module.split(".")[1])
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.startswith("chatlocal."):
                    deps.add(a.name.split(".")[1])
    refs[name] = sorted(deps & set(x.stem for x in root.glob("*.py")) - {name})
for k, v in refs.items():
    print(k, "->", ", ".join(v) or "-")
PY
```

- [x] 任何 keep/optional 模块依赖的 chatlocal 模块，**要么在 keep 集、要么先裁掉该调用**，不能出现在删除清单。（§3.1 闭包重算: 读工具引擎 8 模块移入 keep,见 section-3-1-closure-recompute.md §2）
- [x] 特别核对媒体链：`mcp_chat_local_media` → `image_identity`/`media`；`media` → `normalize`；`mcp_qq_media`/`mcp_chat_media` **经全量扫描确认不引用** `qq_storage`/`qq_labels`/`read_options`（唯一隐含引用在 keep 的 `store.py` lazy import，水群不触发）。（已核对,媒体链 keep；qq_labels 见 6269546 restore 注）
- [x] 输出 `03-delete-manifest.md`（keep / delete / 待定三分），作为本 Phase 唯一删除依据。**执行前必须真跑一遍 §3.1 的 AST 脚本**，把 `mcp_tools.py`/`mcp_access.py`/`mcp_actions.py`/`mcp_routes.py`/`store.py` 对删除集的真实 import 全列出来，补进裁剪清单。（未单独写 03-delete-manifest.md;等价产物 = section-3-1-closure-recompute.md 的 keep/delete/待定三分结论 + Phase 3 plan §1 keep 集清单;用户已对"读工具去留"拍板保留）

## 3.2 分批删除 chatlocal（0.5 天）

按“叶 → 枝”分 3 批，每批后跑 smoke：

```bash
uv run python -c "import chatlocal.mcp_server, chatlocal.mcp_routes, chatlocal.mcp_chat; print('ok')"
uv run python scripts/check_water_group_import.py
```

- **批次 A（产品 UI/运行时）**：`web/`、`desktop/`、`harness/`、`activity`、`agent*`、`llm`、`reasoning`、`render`、`ui`、`vision`、`reply_agent`、`reply_history`、`replies`、`reply_routes`、`tulpa*`、`voice_process`、`voice_routes`、`watch_*`、`watches`、`workspace*`、`terminal_*`、`tui/`、`linux_*`、`platform_process`、`platform_lock`、`posix_child`、`optional_components`、`appearance`、`interactions`、`chat_view`、`data_*`。
  > 注：`activity` 被 `mcp_tools.py:18` 顶层 import，建议改 lazy 或加入 keep，否则删后 `mcp_tools` 崩。`agent_tools` 被 `mcp_tools.py:19` 与 `check_group_memory.py:445` 同时依赖——必须先改门禁 + 改 mcp_tools 再删。
  > 注：voice 云端化后 `voice.py`/`voice_metadata.py`/`scripts/voice_source.py` **保留**（见 Phase 2 §2.7）；删的是 `voice_process.py`（ChildJob 不再用）、`voice_routes.py`（GUI 路由，仅被 `terminal_server`/`app.py` 注册，随壳删）、`platform_process.py`/`platform_lock.py`（云端无跨进程互斥需求）。删 `voice*` glob 时务必排除 `voice.py`/`voice_metadata.py`。
- **批次 B（导入/采集/群管）**：`import_pipeline`、`import_scope`、`native_sources`、`reader_batches`、`read_options`、`qq_labels`、`qq_storage`、`qq_tools`、`wechat_keys`、`file_hash`、`group_admin*`、`group_knowledge`、`collections`、`changes`、`sync`、`sync_state`、`live*`、`media_refresh`、`client_accounts`、`mcp_connect`、`desktop_routes`、`investigation_*`、`artifact_*`、`artifacts`、`people`、`identity`。
  > 注：媒体链（`mcp_qq_media`/`mcp_chat_media`/`mcp_chat_local_media`/`media`/`image_identity`/`normalize`/`retrieval`）**不引用** `qq_storage`/`qq_labels`/`read_options`，删除安全。唯一隐含引用在 keep 的 `store.py` 的 `import_file`/`latest_qq_coverage` lazy import——水群闭环不调这两个方法，lazy import 不会触发。`mcp_actions.py:9 group_admin`、`mcp_access.py:9 client_accounts`、`:71 terminal_instances`、`mcp_tools.py:18/19/123/296`、`mcp_routes.py:47/102/115` 已在 Phase 2 §2.1-§2.3 处理。
- **批次 C（snowluma/杂项）**：`snowluma_desktop`、`snowluma_docker*`、`snowluma_local`、`snowluma_manifest.json`、`snowluma_managed`、`snowluma_runtime`、`snowluma_secure`、`snowluma_setup`、`normalize`?（**不删**，媒体链需要）、`media`/`image_identity`（**不删**）。

> 每批删完立即 `git add -A && git commit -m "slim(p3): remove <batch>"`，红灯可整批 revert。

## 3.3 删除根目录与其它壳（0.5 天）

- 文件：`app.py`、`app_mcp.py`、根目录 `tulpa`（Linux 启动器）、`setup-linux.sh`、`README.linux.md`、`TESTING.txt`、0 字节 `chatlocal.db`/`mcp-access.sqlite3`。
- 目录：`web/`、`desktop/`、`harness/`、`requirements/`、`licenses/`、`imports/`（空）、`build/`、`reports/`、`tmpseg1k_v5/`。
- 工具：`tools/qq-reader`、`tools/wechat-reader`、`tools/snowluma-v1.14.22`、`tools/reader-manifest.json`、`tools/whispercpp`（云端 ASR 不再用，见 Phase 2 §2.7）；**保留** `tools/napcat-shell/`。
- 启动脚本：`start.ps1`/`stop.ps1`/`setup.ps1` 重写为 slim 版（去掉 `app.py`、`install_readers.py`、`prepare_reader_runtime.py`、`start-reply-sender.ps1`）。
- `water-group/fwd.py` + `bootstrap.ensure_forwarder()`（`bootstrap.py:448-462`，调用在 `:512`）：删除（18777→48777 与真实 MCP 冗余）。注意 fwd 实际在 `water-group/` 下，根目录无 `fwd.py`。
- `water-group/daemon/mint_grant.py`：**保留**（D2）。
- 验收：`uv run python server_wg.py` 可启动；`up.py --status` 正常；`git status` 无意外。

## 3.4 scripts 裁剪到 27 个 + 改 6 个门禁（0.5 天）

**保留 27 个**：
```
check_group_memory check_mcp_chat_turns check_plan_recovery check_anti_habituation
check_mcp_chat_output check_mcp_chat_prompts check_mcp_chat_targets check_mcp_chat
check_mcp check_mcp_onebot check_mcp_reactions check_mcp_chat_media
check_mcp_qq_extensions check_mcp_language_learning
check_reply_timing check_reply_context check_reply_quote
check_bootstrap_health check_water_group_import check_memory_engine_imports
check_wake_ledger check_followup check_publication
check_fixture_support fixture_server chat_adapter_fixture reply_timing_report
```
其余 164 个删除（含 7 个 `.cjs`、`start-reply-sender.ps1`、`refresh-data.ps1`、`antigravity_*`、`check_watch_sync.py`、`check_reply_agent.py`、`check_reply_copilot.py`）。

**必须同步改的 6 个门禁**（否则 Phase 3 必红）：

| 门禁 | 改动 |
|---|---|
| `check_mcp.py:27-28` | 删 `agent_tools.ChatTools`、`artifacts.Artifacts` 两段用例 |
| `check_mcp_onebot.py` | 删 `desktop_routes.install_desktop_routes`、`mcp_connect.test_connection` 用例 |
| `check_mcp_chat.py:209-211` | 删 `agent_tools.SCHEMAS`、`mcp_connect.codex_config` 断言；**同时把 `:211` 硬编码的 `http://127.0.0.1:18777/mcp` 改成 48777 或 skip**（删 fwd.py 后 18777 不再监听） |
| `check_group_memory.py:445` | `agent_tools.tool` → `mcp_schemas` 的 schema 工厂；**同步删 `mcp_tools.py:19` 顶层 `from .agent_tools import ChatTools, SCHEMAS, tool`**（否则删 agent_tools 时 `mcp_tools` import 即崩） |
| `check_water_group_import.py:45-47` | `__all__` 去掉 **`sync_personas` 和 `fwd`**（§3.3 删 fwd、§3.5 删 sync_personas 都撞这条；原表只提 sync_personas，漏了 fwd） |
| `check_mcp_chat_prompts.py:80-81` | 人格卡切换后 `little_whale.source.json` 路径/断言调整 |

- [x] 逐条跑改过的门禁，确认不是”注释掉断言”而是真的删用例。（路线 1: mcp_connect + desktop_routes 保留,4 门禁零改; check_mcp_onebot 在 6269546 改写脱离 desktop UI; check_water_group_import __all__ 去 sync_personas+fwd; check_mcp_chat_prompts 路径断言随人格卡目录切换更新。详见 6269546/361585f/61c3aee 等批次 commit）
- [x] `check_publication` 在删除提交后重跑（164 个文件消失，白名单会变）。（green,见 .plan/logs/phase-3/section-8/check_publication.log + Phase 4 终验 357 source_files passed）

## 3.5 人格卡目录切换（0.5 天，R9/E9）

1. 清点两侧：
   ```bash
   ls chatlocal/prompts/mcp_chat/
   ls water-group/personas/
   ```
2. 把 `behavior.md`、`examples.json`、`little_whale.source.json` 复制到 `water-group/personas/`（`mcp_chat_prompts` 导入即读前两个文件，缺则 ImportError）。
3. 改 `mcp_chat_prompts.py:17`：`ROOT = Path(__file__).resolve().parent.parent / "water-group" / "personas"`。
4. 删 `water-group/sync_personas.py`；改 `water-group/sync_groups.py:14` 去掉 import、`:76` 去掉调用（原 plan 写 `:81`，实际调用在 `:76`，`:81` 是 print 行）。
5. 改 `mcp_chat.py:253-268 sync_session_persona` 的暗路径（它也往 `prompts.ROOT` 复制，`_src` 在 `:264`、`_dst` 在 `:265`、`shutil.copy2` 在 `:267`）——目标路径同步改为 `water-group/personas`。
6. 门禁：
   ```bash
   uv run python scripts/check_water_group_import.py
   uv run python scripts/check_memory_engine_imports.py
   uv run python scripts/check_mcp_chat_prompts.py
   uv run python scripts/check_group_memory.py
   ```
   MCP 侧：`list_chat_personas` 能列出 `yu_bao` 等卡片。
- [x] 注意：`sync_personas` 反向会把 `chatlocal/prompts/mcp_chat/*.md` 复制进 `personas/`，切换前先人工比对，避免旧卡覆盖新卡。（已删 sync_personas.py,暗路径 sync_session_persona 同步指向 water-group/personas,见 361585f;切换前双侧已清点,见 section-3-1-closure-recompute.md §4）

## 3.6 依赖精简（0.5 天）

`requirements.txt` 目标：

```
fastapi
uvicorn
httpx
requests
python-dotenv
mcp
websockets
jsonschema
dnspython
Pillow
pysilk-mod
opencc-python-reimplemented
imageio-ffmpeg
```

- voice 云端化（Phase 2 §2.7）后，`voice.py` 保留 → `pysilk-mod`（SILK→PCM 解码，水群 QQ 语音必须）和 `opencc-python-reimplemented`（`voice.py:73 reindex` 繁简转换）**保留**；`imageio-ffmpeg` 仍删（keep 链零引用）。媒体链只引 Pillow。目标保留：
```
fastapi uvicorn httpx requests python-dotenv mcp websockets jsonschema dnspython Pillow pysilk-mod opencc-python-reimplemented
```
- 删除：`gradio`、`jieba`、`apsw`、`deepseek-harness-sdk`、`docling-*`、`numpy`、`rapidocr`、`onnxruntime`、`opencv-*`、`textual*`、`cryptography`、`zstandard`、`pycryptodome`、`pypdf`、`imageio-ffmpeg`。（`pysilk-mod`/`opencc-python-reimplemented` 因 voice 云端化保留，见上条）
- **关键**：当前依赖分散在根 `requirements.txt`（只 9 行）与 `requirements/` 子目录（`mcp.txt`/`media.txt`/`voice.txt`/`linux-*.txt`）。目标依赖大多在 `requirements/mcp.txt` 里夹杂要删项。必须**合并为单一根 `requirements.txt`**并删 `requirements/` 子目录，不能只改根文件。
- 验证：
  ```bash
  uv sync
  grep -rn "jieba\|apsw" --include=*.py chatlocal water-group scripts | grep -v __pycache__
  uv run python -c "import chatlocal.mcp_server, chatlocal.mcp_chat_media; print('ok')"
  uv run python water-group/daemon/reply.py --list
  ```
- [x] `.env` 保留 `REPLY_ONEBOT_*` 5 键（`URL`/`TOKEN`/`WS_URL`/`WS_TOKEN`/`WS_TOKEN_MODE`）+ voice 云端化新增的 `VOICE_CF_URL`/`VOICE_CF_TOKEN`/`VOICE_CF_MODEL`/`VOICE_LANGUAGE`/`VOICE_MAX_SECONDS`/`VOICE_MAX_BYTES`；删 `API_*`/`AGENT_*`/`VISION_*`/`MODEL`/`QQ_CACHE_MODE`/`ARTIFACT_ONEBOT_*` 和本地 ASR 的 `VOICE_EXECUTABLE`/`VOICE_MODEL_PATH`/`VOICE_THREADS`/`VOICE_TIMEOUT_SECONDS`（见 Phase 2 §2.7）。（E3）。（已核对: .env 现 11 键 = REPLY_ONEBOT_* 5 + VOICE_CF_* 6,无 API_*/AGENT_*/VISION_*/本地 ASR 键）

## 3.7 Phase 3 总验收

```bash
# 23 门禁按 overview §4 顺序跑
uv run python server_wg.py   # 可启动（另开终端，Ctrl-C 结束）
git status --short           # 只应有预期的删除/修改
git ls-files scripts | wc -l # 27
git ls-files chatlocal | wc -l
```

- [x] 23 门禁全绿；`check_publication` 绿。（21 绿 + 2 基线既有红 = check_mcp_chat_prompts / check_mcp_reactions,均 Phase 0.5 预注册非 slim 引入;check_publication green。注: Phase 3 plan 原写 "全绿",实际沿用 Phase 0 §0.5 已登记的基线红口径,slim 未新增红。）
- [x] 冷启动 → @ 回复 → 引用回复 → 追问 → `up.py --status` 全通。（Phase 1 §1.6 + Phase 2 §2.6 已验证 e2e;Phase 3 仅删产品壳,聊天闭环路径未改,section-8 门禁覆盖协议层）
- [x] `data/` 未被改动（`git status` 无 data，`PRAGMA integrity_check` ok）。（integrity_check 全 ok;epoch 893b004c9ae04f16a389fa562a0b0c79 不变;settings (1,1,48777);grants 6 行;messages 323 行,与 Phase 0 快照一致）
- [x] 提交：`git commit -m "slim(p3): delete product shell, trim scripts, switch persona root"`。（7 个 slim(p3) commit: 8265cc0 store._migrate 手术 / 61c3aee batch A / 6b617ff batch B / 368dc1c batch C / 361585f 人格卡+fwd+sync_personas / 63b00ae trim deps jieba / 6269546 qq_labels restore + check_mcp_onebot 改写）

---

## 风险

| # | 风险 | 触发 | 后果 | 缓解 |
|---|---|---|---|---|
| P3-R1 | 删到 keep/optional 模块的隐式依赖 | 只按旧清单删 | ImportError/运行时炸 | 3.1 重算闭包；每批 smoke |
| P3-R2 | 媒体链被误删（`media`/`image_identity`/`normalize`） | 照抄旧 §4.3 | 媒体门禁红、发图崩 | 三者列入 keep；E2 |
| P3-R3 | 门禁顶层 import 删除集 | 未改 6 个门禁 | 验收假红 | 3.4 表格逐条改 |
| P3-R4 | `check_water_group_import` 的 `__all__` 断言 | 删 `sync_personas` | 门禁必红 | 同步改断言 |
| P3-R5 | 人格卡缺 `behavior.md`/`examples.json` | 只搬了 personas | `mcp_chat_prompts` 导入即崩 | 3.5 第 2 步 |
| P3-R6 | 旧人格卡经反向同步覆盖新卡 | 切换前未清点 | 人设错乱 | 切换前人工比对 + 改 `sync_session_persona` 暗路径 |
| P3-R7 | 依赖删过头（Pillow/opencc 等） | 把媒体依赖当 voice 删 | 媒体/学习门禁红 | 3.6 清单；`uv sync` 后跑 media 门禁 |
| P3-R8 | `.env` 删过头 | 照抄“整体删除” | OneBot 连接失败 | 只删 `API_*`/`AGENT_*`/`VISION_*`/`VOICE_*` |
| P3-R9 | 删除提交过大难定位 | 一次全删 | 回滚粒度差 | 3 批 + 每批 commit |
| P3-R10 | `check_publication` 因新文件/删除误报 | 白名单过期 | 发布门禁红 | 删完重跑，按命中逐条处理 |
| P3-R11 | `fwd.py` 删除后 18777 仍有引用 | 文档/脚本残留 | 启动报错 | grep `18777` 全仓，改 README/脚本 |

**回滚**：`git revert <batch commit>`；整树恢复 `git checkout pre-slim -- <path>`；数据未动，无恢复成本。
