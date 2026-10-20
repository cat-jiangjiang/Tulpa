# 水群常驻守护

挂机水群不该依赖 Agent 的某一次工具调用活着。MCP 长轮询一旦超时或断线，
监听就断了；这里把「等消息」和「保活 QQ」都挪到独立进程里，Agent 只负责写回复。

## 进程

| 脚本 | 职责 |
| --- | --- |
| `supervisor.py` | 总监护，拉起并重启下面两个子进程；采集器靠心跳判活，卡死也会被杀掉重启 |
| `collector.py` | 常驻 `wait_chat_messages`（`mention_only`），每条消息先落盘再交给 Agent |
| `watchdog.py` | 每 10 秒 `get_login_info` 健康探测；无响应就按端口反查 PID、杀掉、重新注入 QQ |

配套脚本：

- `session.py`：确保目标群有活的持续会话（会话绑定在 grant 上，grant 轮换会一起没）
- `mint_grant.py`：本地 MCP 授权失效时重新签发，沿用上一次的权限范围
- `mcp_client.py`：最小 JSON-RPC 客户端，遇到 `denied` 自动续签并重放一次

## 状态目录

默认 `.tmp/`，可用 `TULPA_WG_STATE` 改。Agent 与守护进程通过这些文件协作：

| 文件 | 含义 |
| --- | --- |
| `wg_events.jsonl` | 已送达的消息流水，按 event id 去重，Agent 断线期间也不会丢 |
| `wg_state.json` | 最近一次 `wait` 的完整返回，含 `input_batch` |
| `wg_ack.txt` | Agent 已处理到的游标，采集器下一轮用它确认进度 |
| `wg_pause` | Agent 规划/发送期间存在，保证批次不被下一轮等待打散 |
| `wg_heartbeat` | 采集器最后一次完成循环的时间，供总监护判活 |
| `wg_session.txt` | 当前会话 id |
| `mcp_token.txt` | 当前授权 token |

## 启动

```bash
.venv/Scripts/python.exe water-group/daemon/supervisor.py
```

## 为什么用 HTTP 探测而不是端口探测

QQ 被注入后会出现「端口还在 LISTENING、进程也在，但 OneBot 不再应答」的假活。
只探端口会把假活当成健康，于是守护进程全程沉默。这里探的是 `get_login_info`
的真实返回，实测假活能被识别并在约 8 秒内重新上线。
