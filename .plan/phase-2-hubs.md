# Phase 2 — 裁剪三个枢纽（核心风险区）

> 目标：把 `mcp_tools` / `store` / `mcp_access` 从产品壳里解耦，同时**保持 daemon 与门禁全绿**。
> 预计：3-4 天（比原方案多 1 天，因复核发现 13 处硬依赖 + WGStore 需兼容 `messages`/`import_file`）。
> 前置：Phase 1 完成、端到端 @ 回复正常。原则：**每个子步骤独立 commit + 独立门禁**，红灯只回退该步。

---

## 2.0 前置检查

```bash
uv run python scripts/check_memory_engine_imports.py
uv run python scripts/check_water_group_import.py
uv run python scripts/check_mcp_chat_output.py
```

- [ ] 三条结构/纯函数门禁绿；否则先修环境再动枢纽。
- [ ] 记录当前 `data/mcp-access.sqlite3` 的 `grants`/`settings` 行（Phase 0 快照可复用）。

---

## 2.1 低耦合修复（0.5-1 天）

目标：清掉“函数内 lazy import 删除集模块”的 8 处，避免 Phase 3 运行时炸。

### 2.1.1 配置来源：`config.json` 优先，`.env` 回退

新增 `chatlocal/config.py::wg_config()`（只读缓存）：

```python
def wg_config():
    path = ROOT / "water-group" / "config.json"
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
```

改三处读取顺序（保持 `.env` 回退，**不要删 .env 的这些键**）：
- `onebot.py:36-47 configuration()`：`wg_config()["onebot_http_url"/"onebot_token"]` → 回退 `REPLY_ONEBOT_URL/TOKEN`。
- `onebot_events.py:18-30 EventReceiver`：`ws_url/ws_token` 同理，回退 `REPLY_ONEBOT_WS_*`。
- `message_sender.py:22-30 sender_config()`：同上。

**验收**：`check_mcp_onebot.py`、`check_reply_quote.py` 绿；临时把 `.env` 改名后 `onebot.configuration()` 仍能从 config.json 取到值。

### 2.1.2 内联 snowluma（3 个文件、6 处）

- `onebot.py:37/65`、`onebot_events.py:20/161`、`message_sender.py:23/56`。
- 把 `from .snowluma_managed import connection/guard` 替换为本地 no-op：
  ```python
  def connection():
      return None

  @contextmanager
  def guard(*_a, **_kw):
      yield
  ```
- 保留 `_managed` 字段语义（恒 False）；不要删 `message_sender` 里对 `_managed` 的分支判断（Phase 3 再清）。
- **验收**：`check_mcp_onebot`、`check_reply_quote`、`check_mcp_chat_media` 绿；真实发一条消息。

### 2.1.3 `mcp_routes.py` 去掉 `desktop_routes` 依赖

`mcp_routes.py:47-50` 的 `read_product_settings()` → 改成：

```python
cfg = wg_config()
onebot_configured = bool(cfg.get("onebot_http_url") and cfg.get("onebot_token"))
```

不要复制 `desktop_routes.py:20` 的实现（它自己 import `snowluma_managed`）。

### 2.1.4 `mcp_actions.py` 清群管理/检索依赖（模块级，必改）

- 删 `from .group_admin import GroupAdmin`（`:9`）与整段群管理分支（`prepare()` 内）。
- 删 `scoped_target()` 与 `from .retrieval import scope_sql`（`:12`）——水群发送走 `mcp_chat.py:960` 的 `target_resolver`，不经此路径。
- 保留 `dump()`（被 6 个媒体模块使用：`mcp_chat_turns`/`mcp_qq_media`/`mcp_qq`/`mcp_reactions`/`mcp_chat_media`/`mcp_sticker_library`）；**不要**把它搬进 `WGTools`。

### 2.1.5 `mcp_access.py` 清 `terminal_instances`

`mcp_access.py:71` 的 lazy import → 换成 5 行 socket 端口检查，或直接删（`configure()` 只在 GUI 用）。

**2.1 提交**：`git commit -m "slim(p2): decouple keep modules from snowluma/desktop/group_admin"`

---

## 2.2 WGStore（1-1.5 天，Phase 2 最大工作量）

### 决策（复核修正，优先于主文档附录 B）

- **原地精简 `chatlocal/store.py`**，不新建 `wg_store.py`：删除集模块与 `scripts/check_*.py`（约 60 个）仍 `from .store import Store`，保留类名 `Store` 并加 `WGStore = Store` 别名，Phase 3 收尾再统一改名/删除调用。
- **`local_change_meta` 必须建在 `data/chats.sqlite3`**（现由 `changes.py:7` 创建），不是 `mcp-access.sqlite3`——否则 epoch 语义变化，grant 全量失效。
- 必须补 **`messages` 表**（同 schema 空表），否则 `mcp_routes.py:46/78/243`、`mcp_access.py:118`、`mcp_actions.py:55` 直接 500。
- 必须补 **`import_file()` 最小版**：`check_mcp_chat.py:185`、`check_mcp.py:87/147/217` 靠它播种。
- 必须保留 **`tokens()` 存根**（无 jieba 的简单分词），供 Phase 2 期间 `agent_tools`/`artifacts` 仍被 `check_mcp.py` 顶层 import 时使用；Phase 3 删。
- 连接参数与现实现对齐：`timeout=30`、`foreign_keys=True`、`row_factory=sqlite3.Row`（草图 `timeout=10` 会在 collector 高频写下 `database is locked`）。

### 步骤

1. 取旧实现做参照：
   ```bash
   git show pre-slim:chatlocal/store.py | sed -n '1,120p'
   git show pre-slim:chatlocal/store.py | grep -n "CREATE TABLE" 
   ```
2. 重写 `store.py`（≈70 行）：
   - `__init__(path=DB)`：建父目录 → `connect()` → `PRAGMA journal_mode=WAL` → 建 `local_change_meta` + `messages`（列名照抄旧 schema）。
   - **删掉顶层 `import jieba`（`store.py:8`）**：jieba 在 Phase 3 §3.6 删除列表里，顶层 import 会让 `import chatlocal.store` 直接 ImportError，连锁崩 `mcp_chat_local_media`/`mcp_tools`/`mcp_routes`。`tokens()` 存根改用正则分词，不 import jieba。
   - `connect()`：`sqlite3.connect(self.path, timeout=30)`，`PRAGMA foreign_keys=ON`，`row_factory=Row`，`@contextmanager` 提交/回滚。
   - `tokens(text)`：正则分词，不 import jieba（仅兼容，不进 DB）。
   - `import_file(path, platform="", conversation="", ...)`：复制旧实现中“解析 JSON/JSONL → 写 messages + dedup”的段落，**删掉** media/sticker/artifact/voice/import_batch 分支；保留返回值结构（门禁会读）。 注意 `import_file`/`latest_qq_coverage` 内有对 `qq_labels`/`qq_storage`/`read_options`/`tulpa`/`import_pipeline`/`sync_state`/`live_state` 的 lazy import——水群闭环不调用这两个方法（只用 `connect`/`stats`/`context`/`message`/`conversations`），lazy import 保留无害，但不要新增对这些模块的顶层依赖。
3. 逐条跑门禁：
   ```bash
   uv run python scripts/check_plan_recovery.py
   uv run python scripts/check_mcp_chat_turns.py
   uv run python scripts/check_mcp_chat.py
   uv run python scripts/check_mcp.py
   uv run python scripts/check_group_memory.py
   ```
4. 真实链路：重启栈 → collector 写 `chat_inbox` → 一次 @ 回复（确认 `chat_sessions`/`chat_turn_plans` 正常）。

- [ ] `data/chats.sqlite3` 里 `local_change_meta.epoch` 与 Phase 0 快照**一致**（未重置）。
- [ ] `messages` 表存在且列名与备份库一致：`PRAGMA table_info(messages)` 对比。
- [ ] 5 条门禁绿；`check_mcp.py` 若因 `artifacts` 仍 import `store.tokens` 而红，确认 `tokens()` 存根已提供。

**提交**：`git commit -m "slim(p2): replace store with WGStore (messages/import_file/local_change_meta)"`

---

## 2.3 WGTools（0.5-1 天）

### 关键事实

- `mcp_tools.py` **不在任何 keep/delete 清单**，但 `mcp_server.py:19` 硬引用 `MCPTools`/`INSTRUCTIONS` → 必须**原地改写**，不能删文件。
- `mcp_tools.py` 顶层/函数内还引用 4 个删除集模块，**必须在 §2.3 一并处理**，否则 Phase 3 删它们时 `import chatlocal.mcp_tools` 崩：
  - `:18` `from .activity import evidence_access`（批次 A；`activity` 仅 24 行，**建议直接加入 keep 集**）
  - `:19` `from .agent_tools import ChatTools, SCHEMAS, tool`（批次 A；但 `check_group_memory.py:445` 也依赖 `agent_tools.tool` → Phase 3 §3.4 改门禁用 `mcp_schemas` 后，此处顶层 import 也要同步删/换）
  - `:123` `from .qq_tools import schemas`（批次 B；lazy，水群不用该 scope，保留无害）
  - `:296` `from .vision import frames_for`（批次 A；lazy 在 `read_image` 工具内，水群不用，删 vision 后该分支运行时崩，可接受但要裁掉调用）
- `check_mcp_chat_turns.py:245` 依赖 `mcp_tools.argument_problems`（实际定义在 `mcp_tools.py:40`）→ 必须保留该符号。
- 媒体保留 → `MEDIA_TOOLS`/`REACTION_TOOL`/`QQ_TOOLS` 继续注册；`QQ_WRITES`/`MEDIA_WRITES` 仍从 `mcp_qq.py`/`mcp_chat_media.py` 导入。

### 步骤

1. 读旧 `mcp_tools.py` 的 `MCPChatTools.chat_call/chat_media_call/qq_call/result/argument_problems`，原样迁到 `WGTools`（不要重写逻辑，只删分支）。
2. 删除：`from .activity import evidence_access`（或把 `activity` 加入 keep）、`from .agent_tools import ...`（`:19`，顶层）、`from .retrieval import scope_sql`、`READ_TOOLS`、`ArtifactTools` 等；`:123 qq_tools`/`:296 vision` 是 lazy，水群不用，保留分支但裁掉死调用即可。
3. 保留：`INSTRUCTIONS`（`mcp_tools.py:66`）、`CHAT_TOOLS` 分发、媒体/反应/QQ 分发、`idempotency_key`、`argument_problems`（`:40`）。
4. `mcp_server.py`：`self.tools = WGTools(access)`；`readOnlyHint` 一行继续用 `QQ_WRITES`/`MEDIA_WRITES`（媒体保留，导入不变）；`self.tools.actions.recover()` 保留（`mcp_actions.recover` 自建 `operations*` 表，自洽）。
5. 门禁：
   ```bash
   uv run python scripts/check_mcp.py
   uv run python scripts/check_mcp_chat_turns.py
   uv run python scripts/check_mcp_chat.py
   uv run python scripts/check_mcp_chat_targets.py
   ```

**提交**：`git commit -m "slim(p2): WGTools replaces MCPTools"`

---

## 2.4 mcp_access（0.5 天）

### 4 个改动

1. **`accounts()`**（`mcp_access.py:80-81`）：不再调 `client_accounts.bound_account`。改为：
   ```python
   def accounts(self):
       account = str(wg_config().get("self_account") or "")
       return {"qq": account} if account else {}
   ```
   保持返回结构与旧 `bound_account` 一致（grant 写入的 `accounts` JSON 不变）。
2. **`plan()` 必须保留**（主文档 §3.3 勘误）：
   - `plan()` 被 `mcp_chat.py:288/455/573`、`mcp_chat_details.py:130`、`mcp_actions.py:53`、`mcp_chat_media.py:435` 使用，只用到 `.start/.end` 窗口。
   - 保留 `chatlocal/retrieval.py` 的 `Plan`/`date_bound`/`scope_sql`（≈35 行），删 `make_plan/retrieve` 等重逻辑；或把这 3 个符号内联进 `mcp_access`。**不要删模块**。
3. **epoch 保持读表**（不改成常量）：`epoch()` 继续读 `local_change_meta`，值沿用现库（Phase 0 快照）。改常量会触发全量 401（R2/E8），留到 Phase 4 评估。
4. **`mint_grant.last_scope()` 兜底**（`daemon/mint_grant.py:32-44`）：grants 表为空时不再 `SystemExit`，改用内置 `DEFAULT_SCOPE`（chat/send/onebot/manage/chat_* 全开），保证首次自愈可用。

### 门禁

```bash
uv run python scripts/check_mcp.py
uv run python scripts/check_plan_recovery.py
uv run python scripts/check_mcp_chat_turns.py
uv run python scripts/check_group_memory.py
```

**提交**：`git commit -m "slim(p2): mcp_access accounts/plan/epoch fixes"`

---

## 2.5 mcp_routes / mcp_server 收口（0.5 天）

**本 Phase 只做运行时必需的**，路由删除留到 Phase 3（避免门禁假红）：

- 保留：`/api/mcp`（status，`messages` 计数加 try/except 兜底）、`/api/mcp/connections`、`/api/mcp/chats`、`/api/mcp/chats/{sid}/stop`、`/api/mcp/chats/{sid}/learning*`。
- `mcp_routes.py:102` `/api/mcp/test` 与 `:115` `/codex`：**Phase 3 与门禁用例一起删**（本 Phase 保留，lazy import 无害）。
- 删 `desktop_routes.read_product_settings` 调用已在 2.1.3 完成。
- `mcp_server.py` 的 `WGTools` 切换已在 2.3 完成；本步只做 import 清理与 smoke。

**门禁（全量 23 条）**：按 `00-overview.md` §4 顺序跑；重点确认 `check_mcp_onebot`、`check_mcp_chat_media`、`check_mcp_qq_extensions`、`check_mcp_language_learning` 不因 2.1 的改动而红。

---

## 2.6 Phase 2 总验收

- [ ] 23 门禁全绿（日志在 `.plan/logs/phase-2/`）。
- [ ] 冷启动 → collector 建会话 → @ 回复 → 引用回复 → 追问窗口（`followup.py --show`）全通。
- [ ] `data/` 表结构对比 Phase 0 备份：`chat_*` 表列无缺失；`messages` 列一致；`grants` 行数不减少。
- [ ] 无新增 401：`grep -c "401" water-group/logs/collector.log`（或 daemon 日志）与基线持平。
- [ ] `git log --oneline` 至少 5 个 `slim(p2)` commit，可逐条回退。

---

## 2.7 voice 转写云端化（1 天，复核新增）

> 决策：**保留 voice 转写能力**（水群 QQ 语音条 → 文字让 Agent 能读懂），但把 ASR 后端从本地 `whisper.cpp` 换成云端 Cloudflare Worker（`https://whisper.yuuverne.site`，已部署，token `00ba9ee6...`，turbo 模型实测返回正常中文）。
> 不挂 TTS / 不发语音（当前代码零 TTS，不在本次范围）。

### 依赖闭包（复核 voice.py 实际引用）

- **加 keep**：`voice.py`（改 ASR 段）、`voice_metadata.py`（`voice.py:38 register` + `normalize.py` 都依赖）、`scripts/voice_source.py`（定位 QQ 原生语音 + SILK→WAV 解码）。
- **仍删（批次 A）**：`voice_process.py`（`ChildJob` 不再用）、`platform_process.py`（`owned_popen` 不再用）、`platform_lock.py`（`worker.lock` 跨进程互斥不再需要——云端并发由 Worker 端处理）、`voice_routes.py`（GUI HTTP 路由，水群 MCP 不走它，`terminal_server`/`app.py` 随壳删）。
- **`scripts/voice_source.py` 裁剪**：删 `locate_wechat` 分支（依赖 `tools/wechat-reader`/`wechatauto`，水群不用），保留 `locate_qq`（纯 stdlib，读 `data/qq-snapshot-info.json` + `Path(nt_data/Ptt)`，**不依赖 `tools/qq-reader`**）与 `decode`（SILK→WAV，用 `pysilk`）。
- `tools/qq-reader`/`tools/wechat-reader`：§3.3 仍删（`locate_qq` 不 import 它们）。
- `whisper.cpp`（`tools/whispercpp`）+ ggml 模型（`.cache/voice-models/`）：不再需要，§3.3 删 `tools/whispercpp`。

### 代码改造点（`chatlocal/voice.py`）

1. **`config()`（`:82-95`）**：删 `model`/`executable`/`threads`；改读云端配置：
   ```python
   def config():
       from dotenv import dotenv_values
       values=dotenv_values(ROOT/'.env')
       def val(k,d):return os.environ.get(k,values.get(k) or d)
       language=val('VOICE_LANGUAGE','zh')
       if language not in ('zh','auto','en'):raise ValueError('VOICE_LANGUAGE 必须为 zh、auto 或 en')
       return dict(language=language,
           cf_url=val('VOICE_CF_URL','https://whisper.yuuverne.site').rstrip('/'),
           cf_token=val('VOICE_CF_TOKEN',''),
           cf_model=val('VOICE_CF_MODEL','whisper-large-v3-turbo'),
           max_seconds=int(val('VOICE_MAX_SECONDS','180')),
           max_bytes=int(val('VOICE_MAX_BYTES',str(16*1024*1024))))
   ```
2. **`profile(cfg)`（`:97-100`）**：云端指纹，不含本地文件 hash：
   ```python
   def profile(cfg):return 'cf-whisper:'+cfg['cf_model']+':'+cfg['language']
   ```
3. **`_run()` 的 ASR 段（`:219-235`）**：删"本地 ASR 未安装"检查 + `self._child([whisper-cli...])` 子进程；改成 httpx POST：
   ```python
   fingerprint=profile(cfg)
   with self.store.connect() as db:cached=db.execute('SELECT * FROM voice_transcripts WHERE audio_sha256=? AND profile=?',(row['audio_sha256'],fingerprint)).fetchone()
   if cached:text,language=cached['transcript'],cached['language']
   else:
       import httpx
       wav_bytes=wav.read_bytes()  # voice_source.py 已输出 16kHz 单声道 WAV
       r=httpx.post(f"{cfg['cf_url']}/raw?task=transcribe&language={cfg['language']}&model={cfg['cf_model']}",
           headers={'Authorization':f"Bearer {cfg['cf_token']}",'Content-Type':'application/octet-stream'},
           content=wav_bytes,timeout=cfg['max_seconds'])
       if r.status_code==401:raise VoiceError('云端 ASR token 无效，请检查 VOICE_CF_TOKEN。')
       if r.status_code!=200:raise VoiceError('云端 ASR 请求失败('+str(r.status_code)+')，可重试。')
       data=r.json().get('response',{})
       text=(data.get('text') or '').strip()
       language=data.get('transcription_info',{}).get('language','auto')
       if not text:raise VoiceError('云端未识别出语音文字；请试听原音频。')
       if len(text)>24000:raise VoiceError('转写文本超出单条限制。')
   ```
4. **`_loop()`（`:154-194`）**：删 `worker.lock`（`platform_lock.file_lock`）整段——云端无跨进程互斥需求；保留队列消费结构。
5. **`_child()`（`:112-131`）**：删（不再起 ASR 子进程）；但 `voice_source.py` 子进程调用（`:204` `self._child([sys.executable,str(ROOT/'scripts/voice_source.py'),...],45)`）要保留——用内联 `subprocess.run` 替代或保留精简版 `_child`。
6. **写库**（`:239-243`）：`backend` 列从 `'whisper.cpp'` 改 `'cloudflare'`，`model` 列存 `cfg['cf_model']`。

### `.env` 键调整

- 删：`VOICE_EXECUTABLE`、`VOICE_MODEL_PATH`、`VOICE_THREADS`、`VOICE_TIMEOUT_SECONDS`（本地 ASR 不再用）。
- 保留：`VOICE_LANGUAGE`、`VOICE_MAX_SECONDS`、`VOICE_MAX_BYTES`。
- 新增：`VOICE_CF_URL`（默认 `https://whisper.yuuverne.site`）、`VOICE_CF_TOKEN`（填实际 token）、`VOICE_CF_MODEL`（默认 `whisper-large-v3-turbo`）。

### 依赖（覆盖 §3.6 的 voice 相关结论）

- **保留**：`pysilk-mod`（SILK→PCM 解码，水群 QQ 语音必须）、`opencc-python-reimplemented`（`voice.py:73 reindex` 繁简转换，轻量，可选但留）。
- **删**：`imageio-ffmpeg`（keep 链零引用）、`whisper.cpp`/ggml 模型（不再用）。

### 验收

- [ ] `uv run python -c "from chatlocal.voice import config,profile;cfg=config();print(profile(cfg))"` → `cf-whisper:whisper-large-v3-turbo:zh`。
- [ ] `scripts/check_voice.py` 绿（若它断言本地 ASR 就绪，先改断言为云端可达：`httpx.get(cfg['cf_url']+'/health').status_code==200`）。
- [ ] 真实链路：群里发一条语音 → collector 写 `voice_sources` → `agent_tools._transcribe_voice` → 云端转写 → `voice_transcripts` 落一条 `backend='cloudflare'`。
- [ ] `data/chats.sqlite3` 的 `voice_sources`/`voice_transcripts`/`voice_jobs` 表结构与 Phase 0 备份一致（未改 schema）。

**提交**：`git commit -m "slim(p2): voice ASR to cloudflare worker (drop whisper.cpp/platform_process/voice_process)"`

---

## 风险

| # | 风险 | 触发 | 后果 | 缓解 |
|---|---|---|---|---|
| P2-R1 | 模块级 import 删除集（5 处） | 漏改 `mcp_access:9/10`、`mcp_actions:9/12` | 服务起不来 | 2.1 先修 + `uv run python -c "import chatlocal.mcp_server"` smoke |
| P2-R2 | 函数内 lazy import 删除集（8 处） | 只跑了 happy path | 运行到特定分支才炸 | 2.1.2/2.1.3 全部清；门禁覆盖 media/onebot |
| P2-R3 | WGStore 少建 `messages`/`import_file` | 照抄附录 B | 4 个门禁红 + `/api/mcp` 500 | 2.2 明确要求；用 `PRAGMA table_info` 对比备份 |
| P2-R4 | `local_change_meta` 建错库 | 照抄附录 B 指向 mcp-access | epoch 变 → grant 全量 401 | 必须建在 `chats.sqlite3`；对比 Phase 0 epoch |
| P2-R5 | 连接参数退化（timeout 10/无 FK） | 简化过猛 | collector 高频写 `database is locked` | `timeout=30` + `foreign_keys=ON` + WAL |
| P2-R6 | 删 `plan()` | 照抄主文档 §3.3 | 6 处调用崩 | **保留**；retrieval 只瘦身不删 |
| P2-R7 | 删 `media.py`/`image_identity.py`/`normalize.py` | 照抄 §4.3 | `mcp_chat_local_media` 崩 | 三者随媒体链保留（E2） |
| P2-R8 | WGTools 漏 `argument_problems`/`INSTRUCTIONS` | 重写时只搬分发 | `check_mcp_chat_turns` 红 / MCP 说明丢失 | 2.3 清单逐项核对 |
| P2-R9 | 路由删太早 | 与门禁用例冲突 | Phase 2 假红 | 2.5 只做必需项，删除留 Phase 3 |
| P2-R10 | `store.tokens` 直接删 | 忽略 `check_mcp.py` 顶层 import | Phase 2 门禁红 | 保留无 jieba 存根，Phase 3 删 |
| P2-R11 | token 单槽被覆盖 | 多 Agent mint | collector 401 | 401 自愈保留；`mint_grant` 兜底 scope |
| P2-R12 | 改 epoch 常量 | 想“顺手修” | 全量 401 + 会话重建 | 明确不做，Phase 4 再评估 |
| P2-R13 | 云端 ASR 不可达/超配额 | Cloudflare Worker 宕/Neurons 用尽 | 语音转写全失败（文字不受影响） | `voice.py` 对 401/非 200 抛 `VoiceError` 让单条失败；每日 10k Neurons（≈200+ 分钟音频）足够水群；token 进 `.env` 不入库 |
| P2-R14 | `voice_source.py` 删过头 | 误删 `locate_qq`/`decode` | 无法定位/解码 QQ 语音 | §2.7 明确只删 `locate_wechat`；保留 `pysilk` 依赖 |
| P2-R15 | `voice_routes` 仍被注册 | `terminal_server`/`投app.py` 未随壳删 | import 链残留 | 批次 A 删 `terminal_server`/`app.py` 时同步删 `voice_routes` |

**回滚**：按 `slim(p2)` commit 逐个 `git revert`；数据未动，无需恢复。若已产生新会话数据，回滚后仍兼容（表结构未变）。
