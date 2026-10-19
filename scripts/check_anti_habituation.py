"""Test suite for Phase 4: Persona expression diversity & anti-habituation detection."""
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chatlocal import mcp_chat_prompts as prompts
from chatlocal.mcp_chat_output import compact_wait


def test_habit_detection():
    row = dict(conversation_id="111:group:222", participation="natural", persona="test persona", persona_name="测试")
    now = time.time()

    # Case 1: Diverse messages, no habituation
    recent_diverse = [
        dict(id=1, onebot_message_id=101, is_self=True, content="今天天气不错", received_at=now - 50),
        dict(id=2, onebot_message_id=102, is_self=False, sender_id=222, content="确实挺好", received_at=now - 40),
        dict(id=3, onebot_message_id=103, is_self=True, content="要不要出去转转", received_at=now - 20),
    ]
    batch = [dict(id=4, onebot_message_id=104, sender_id=222, content="走起", segments=[])]
    pkt_diverse = prompts.packet(row, recent_diverse, batch)
    assert not pkt_diverse["social_context"].get("repeated_phrases")
    assert any("【表达去模板化】" in r for r in pkt_diverse["reminders"])

    # Case 2: Repetitive suffix ("草")
    recent_habituated = [
        dict(id=1, onebot_message_id=101, is_self=True, content="这也太搞笑了草", received_at=now - 50),
        dict(id=2, onebot_message_id=102, is_self=False, sender_id=222, content="哈哈哈", received_at=now - 40),
        dict(id=3, onebot_message_id=103, is_self=True, content="绝了草", received_at=now - 20),
    ]
    pkt_habituated = prompts.packet(row, recent_habituated, batch)
    repeated = pkt_habituated["social_context"].get("repeated_phrases", [])
    assert "草" in repeated, f"Expected '草' in repeated_phrases, got {repeated}"
    assert any("【防语癖固化】" in r and "“草”" in r for r in pkt_habituated["reminders"])

    # Case 3: Compact wait delivery
    compacted = compact_wait(dict(pkt_habituated, event="messages", decision_required=True, chat_guidance=pkt_habituated))
    assert any("【防语癖固化】" in r and "“草”" in r for r in compacted["chat_guidance"]["reminders"])
    print("✓ Anti-habituation detection and diversity reminder test passed")


if __name__ == "__main__":
    test_habit_detection()
    print("PASS: anti-habituation test suite passed.")
