# Phase 1 — 最小 server 入口（非破坏）

> 目标：新增 `server_wg.py` 作为唯一后端入口，把 supervisor / bootstrap / ps1 的指向切过去；**不删任何文件、不改任何业务逻辑**。
> 预计：1-1.5 天。前置：Phase 0 完成。产出：后端可从新入口冷启动、MCP 48777 正常监听、daemon 全链路可用。

---

## 1.1 新增 `server_wg.py`（根目录）

关键点（复核结论）：
- `install_mcp_routes(app)` 内部用 lifespan 在**进程内线程**启动 48777 的 MCP（`mcp_routes.py:18-28`）；**安装之后不得再赋值 `app.router.lifespan_context`**，否则 MCP 静默不启动。
- 新库/清空 `data/` 时 `settings` 默认 `(0, 18777)`（`mcp_access.py:42-43`），必须启动时自检修正为 `enabled=1, port=48777`。
- 端口被占时显式 `exit 2`，让 supervisor 重启，而不是“半活”。

```python
"""Water-group slim server entry: HTTP routes + in-process MCP listener."""
from __future__ import annotations

import socket
import sqlite3
import sys

import uvicorn
from fastapi import FastAPI

from chatlocal.config import DATA
from chatlocal.mcp_routes import install_mcp_routes

DB = DATA / "mcp-access.sqlite3"
HTTP_PORT = 47860
MCP_PORT = 48777


def _ensure_settings() -> None:
    # schema 必须与 chatlocal/mcp_access.py:42 的 CREATE TABLE settings 对齐
    # （mcp_access 用 INSERT OR IGNORE，列顺序/列名一致即可；此处显式 DEFAULT 仅为兜底）
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB)
    try:
        db.execute(
            "CREATE TABLE IF NOT EXISTS settings("
            "id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 1,"
            "port INTEGER NOT NULL DEFAULT 48777)"
        )
        db.execute("INSERT OR IGNORE INTO settings(id, enabled, port) VALUES(1, 1, 48777)")
        db.execute("UPDATE settings SET enabled=1, port=? WHERE id=1", (MCP_PORT,))
        db.commit()
    finally:
        db.close()


def _port_busy(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def create_app() -> FastAPI:
    _ensure_settings()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    install_mcp_routes(app)  # 之后不要覆盖 lifespan_context
    @app.get("/")
    def health():
        return {"ok": True, "service": "water-group", "mcp_port": MCP_PORT}
    return app


if __name__ == "__main__":
    busy = [p for p in (HTTP_PORT, MCP_PORT) if _port_busy(p)]
    if busy:
        print(f"port busy: {busy}", file=sys.stderr)
        raise SystemExit(2)
    uvicorn.run(create_app(), host="127.0.0.1", port=HTTP_PORT,
                access_log=False, log_level="info")
```

- [ ] 文件保存为 UTF-8；不要 import `app.py` 或任何 Gradio 模块。
- [ ] 手动验证一次：`uv run python server_wg.py`（先停栈），浏览器/curl `http://127.0.0.1:47860/` 返回 JSON；`netstat` 见 48777。

## 1.2 `supervisor.py` 换入口 + 子进程日志

`water-group/daemon/supervisor.py:41`：

```python
CHILDREN = {
    "db_keeper": HERE / "db_keeper.py",
    "app": Path(os.environ.get("TULPA_WG_SERVER", ROOT / "server_wg.py")),
    "collector": HERE / "collector.py",
    "watchdog": HERE / "watchdog.py",
}
```

同时改 `spawn()`（`supervisor.py:72-81`）：把 `stdout/stderr=DEVNULL`（`:76`）改为追加写 `water-group/logs/<name>.log`（`open(..., "a", encoding="utf-8")`，句柄交给 Popen 并保留引用，避免被 GC 关闭）。`CREATE_NO_WINDOW`（`:35`）保持。

- [ ] 更新文件头 docstring 的进程树说明（`supervisor.py:5` 附近）。
- [ ] 保留 child 名称 `"app"` 不变（`stop.ps1:4` 的 `CommandLine.Contains` 与日志文件名 `<name>.log` 依赖它）。注：`up.py --status` 的 WMI 模式匹配的是 `supervisor\.py|daemon[\\/]up\.py`，**不**依赖 "app" 名。

## 1.3 `bootstrap.py` 去掉 `app.py` 硬编码

`water-group/bootstrap.py:425-446` 的 `ensure_tulpa()`：
- `:431` `start_process_detached([get_python_exe(), "app.py"], ...)` → 指向 `os.environ.get("TULPA_WG_SERVER", "server_wg.py")`（相对仓库根）。
- 启动前先探 47860：已在监听则直接返回（幂等），避免与 supervisor 抢端口。
- 保留 `--status-only` 分支（`:503`）不动（它是删壳后唯一应保留的人工入口）。
- `:448` `ensure_forwarder()` 拉起 `water-group/fwd.py`（实际 start 在 `:456`），Phase 3 删；Phase 1 不处理，确认 18777 未被占用即可。

## 1.4 `start.ps1` / `stop.ps1` 指向新入口

- `start.ps1:12` 的 `app.py` → `server_wg.py`。
- `stop.ps1:2` 的进程匹配 `app.py` → `server_wg.py`（直接替换，不保留 `app.py` 兼容分支——slim 路线不留兼容层）。
- [ ] 顺手检查 `setup.ps1` 是否有 `app.py` 引用（实测当前无，空动作但照跑）。

## 1.5 `requirements.txt` 补 `requests`

daemon 3 个文件依赖（`bootstrap.py:9`、`sync_groups.py:5`、`reply.py:32`），当前只是 gradio 的传递依赖。gradio 一删就断。

> 注：`up.py` 用 stdlib `urllib.request`，不依赖 requests，无需为它着想。

```bash
uv pip show requests | head -5   # 取实际版本
```

- [ ] 在 `requirements.txt` 增加 `requests==<实际版本>`。

## 1.6 冷启动验收（必须逐条过）

```bash
uv run python water-group/daemon/up.py --stop
netstat -ano | findstr ":47860 :48777"        # 应为空
uv run python water-group/daemon/up.py
sleep 8
uv run python water-group/daemon/up.py --status
netstat -ano | findstr ":47860 :48777"        # 两个都在监听
curl -s http://127.0.0.1:47860/               # {"ok":true,...}
tail -40 water-group/logs/app.log             # uvicorn + MCP 启动日志
```

- [ ] `data/mcp-access.sqlite3` 的 `settings` 行是 `(1, 48777)`。
- [ ] `bootstrap --status-only` 的 MCP ping 返回 200/401（`bootstrap.check_tulpa_mcp_health`）。
- [ ] collector 建会话：`.tmp/wg_session.txt` 有值、`.tmp/wg_heartbeat` 是新的（<30s）。
- [ ] 一次真实 @ 回复：`reply.py <event_id> "..."` 成功；`wg_replied.jsonl` 落一条。
- [ ] **自愈测试**：`taskkill /F /PID <server_wg pid>`，15s 内 supervisor 重启，48777 重新监听，`reply.py --list` 仍可用。
- [ ] 失败排查顺序：`app.log` → `settings` 行 → lifespan 是否被覆盖 → 端口是否被 bootstrap 双起。

## 1.7 提交

```bash
git add server_wg.py water-group/daemon/supervisor.py water-group/bootstrap.py start.ps1 stop.ps1 requirements.txt
git commit -m "slim(p1): add server_wg entry, repoint supervisor/bootstrap/ps1"
```

---

## 风险

| # | 风险 | 触发 | 后果 | 缓解 / 回滚 |
|---|---|---|---|---|
| P1-R1 | `server_wg.py` 覆盖了 `app.router.lifespan_context` | 自定义 lifespan/中间件 | 48777 静默不启动，daemon 全 401，而 `up.py --status` 只看端口 | 代码里不定义 lifespan；1.6 用 `netstat`+MCP ping 双验证 |
| P1-R2 | `settings` 仍是 `(0,18777)` | 新库/清空 data | MCP 不启动 | `_ensure_settings()` 启动即修正；日志打印最终值 |
| P1-R3 | `bootstrap.ensure_tulpa` 再起一个入口 | 未改 `bootstrap.py:431` | 47860 端口冲突/双进程 | 1.3 改指向 + 端口占用即跳过 |
| P1-R4 | 子进程 `DEVNULL`，ImportError 不可见 | 未改 `spawn()` | 无法定位启动失败 | 1.2 落 `logs/app.log` |
| P1-R5 | `start.ps1`/`stop.ps1` 仍指 `app.py` | 人工启停 | 绕过新入口或停不掉 | 1.4 同步改 |
| P1-R6 | `requests` 仍靠 gradio 传递 | Phase 3 删 gradio | daemon 全链 ImportError | 1.5 显式加依赖（3 个文件：bootstrap/sync_groups/reply；up.py 用 stdlib 不需要） |
| P1-R7 | token 单槽被别的 grant 覆盖 | 多 Agent 同时 mint | collector 401，自愈依赖 `mint_grant` | 保留 401 重铸；必要时手动 `uv run python water-group/daemon/mint_grant.py` |
| P1-R8 | `fwd.py` 仍被 bootstrap 拉起 | 18777→48777 转发 | 无害但冗余/可能自环 | Phase 3 删；Phase 1 不处理，确认 18777 未被占用即可 |
| P1-R9 | `up.py --status` 的守护进程识别靠 WMI 正则 | 进程名变化 | 状态误报 | 保持 child 名 `app`（`stop.ps1`/日志名依赖）、命令行含 `supervisor.py`；`up.py` 的 WMI 匹配 `supervisor\.py|daemon[\\/]up\.py`，不靠 "app" 名 |

**回滚**：`git revert <p1 commit>`；`supervisor.py` 指回 `app.py`；重启栈。数据未动，无恢复成本。
