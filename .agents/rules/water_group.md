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
     - Replyer: 调用 `send_chat_reply(session_id=..., plan_id=..., bubbles=[{"kind": "text", "text": "..."}])`。

3. **保持监听流水线不中断**：
   - 每次发送完回复后，输出简明回显，**必须立即继续调用 `wait_chat_messages`**。
   - 若遇到 150 秒超时（返回 `idle` 空消息），**静默继续调用 `wait_chat_messages`**，不要退出等待。
