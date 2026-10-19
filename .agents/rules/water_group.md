# QQ 水群专属规则 (Water Group Rules)

当用户提到“水群”、“挂机水群”、“开始水群”、“去群里聊会”等意图时，必须遵循以下 SOP：

1. **零探索冷启动**：
   - 严禁反复执行 `tasklist`、`cat mcp_tools.py` 或查 SQLite 数据库试探参数格式。
   - 先执行 `python water-group/sync_personas.py` 同步人格卡。
   - 直接调用 `tulpa` MCP 工具 `list_chat_sessions`：
     - 若已有活动会话（`active: true`），直接调用 `get_chat_session(session_id=..., response_format="compact")` 瞬间恢复。
     - 若无活动会话，调用 `start_chat_session(conversation_id="<account>:group:<group_id>", persona_preset="little_whale", participation="natural", idempotency_key=...)` 开启。

2. **去 AI 味短句发言**：
   - 每句话 1~10 字，短句打字感，不换行。
   - 严禁客服腔，严禁排比升华，严禁在消息中包含括号内心戏。
   - 收到消息后：
     - Planner: 调用 `plan_chat_reply(session_id=..., batch_id=..., action="reply", intent=...)`。
       - **【状态检查与 Stale 保护】**：调用后**必须检查 `state`**，严禁盲目读取 `plan_id` 或盲目发送：
         - 若 `state == "READY"`：安全获取 `plan_id = plan_res["plan_id"]`，进入 Replyer 发送。
         - 若 `state == "REJECTED"`（此时 `plan_id` 为 `None`，不可调用发送）：
           - 若附带 `updated_chat`（如 `stale` / `needs_refresh`）：**直接提取 `updated_chat["input_batch"]`（及其 `id`）原地重新调用 `plan_chat_reply`**，无需重新调用 wait！
           - 若未附带 `updated_chat`：沿 `recovery.next_call` 刷新上下文或等待后再规划。
     - Replyer: 调用 `send_chat_reply(session_id=..., plan_id=..., bubbles=[{"kind": "text", "text": "...", "quote": true}])`（首条文字带 `"quote": true` 开启 QQ 原生引用回复）。

3. **保持监听流水线不中断**：
   - 每次发送完回复后，输出简明回显，**必须立即继续调用 `wait_chat_messages`**。
   - 若遇到 150 秒超时（返回 `idle` 空消息），**静默继续调用 `wait_chat_messages`**，不要退出等待。
