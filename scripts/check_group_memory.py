"""Comprehensive test suite for group member dynamic memory & personalized targeted reply system.

Tests:
1. SQLite initialization, migrations, and schema creation in MemoryEngine.
2. Auto-seeding from persona markdown (时月/孩董).
3. Member profile search and alias resolution.
4. Explicit command parser (various patterns and negative cases).
5. Explicit command execution and memory updates.
6. Direct interaction piggyback extraction (preferences and identity claims).
7. Idle digest background extraction.
8. Tier 1 (L1 Context) compact synthesized card generation (~80 tokens).
9. Tier 2 (L2 DB Storage) query_member_dossier with group-scoped boundary enforcement.
10. MCP integration: CHAT_TOOLS, schemas, compact_wait preservation, and tool dispatch.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "water-group"))

from memory_engine import MemoryEngine
from chatlocal.mcp_chat import CHAT_TOOLS, schemas as chat_schemas
from chatlocal.mcp_chat_output import compact_wait


def test_schema_and_init(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    assert db_file.exists(), "DB file must be created"
    assert engine.get_member_count() == 0, "Initial count should be 0 before seeding"
    print("✓ 1. SQLite 架构初始化与建表测试通过")


def test_seeding(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    count = engine.seed_from_persona()
    assert count == 2, f"Expected 2 seeded members, got {count}"
    assert engine.get_member_count() == 2

    # Verify 时月
    shiyue = engine.find_profile("时月")
    assert shiyue is not None, "时月 profile must exist"
    assert shiyue["primary_name"] == "时月"
    aliases = json.loads(shiyue["aliases"])
    assert "🐶领导" in aliases and "地牢主" in aliases and "地牢🐷" in aliases
    interests = json.loads(shiyue["interests"])
    assert "原神" in interests and "三国杀" in interests
    memories = json.loads(shiyue["key_memories"])
    assert any("网黄" in m for m in memories)

    # Verify criss / 孩董
    criss = engine.find_profile("criss")
    assert criss is not None, "criss profile must exist"
    criss_aliases = json.loads(criss["aliases"])
    assert "孩董" in criss_aliases and "妃姬杯" in criss_aliases
    criss_interests = json.loads(criss["interests"])
    assert any("🐻男" in i for i in criss_interests)

    # Re-seeding idempotency
    count2 = engine.seed_from_persona()
    assert count2 == 0, "Idempotent re-seed should not insert new rows"
    assert engine.get_member_count() == 2
    print("✓ 2. 角色卡预置成员档案自动种子测试通过 (时月 / 孩董)")


def test_alias_matching(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    # Exact name match
    assert engine.find_profile("时月")["user_id"] == "seed:shiyue"
    assert engine.find_profile("criss")["user_id"] == "seed:criss"

    # Alias matches for 时月
    assert engine.find_profile("🐶领导")["primary_name"] == "时月"
    assert engine.find_profile("地牢主")["primary_name"] == "时月"
    assert engine.find_profile("地牢🐷")["primary_name"] == "时月"

    # Alias matches for criss
    assert engine.find_profile("孩董")["primary_name"] == "criss"
    assert engine.find_profile("妃姬杯")["primary_name"] == "criss"

    # Nonexistent
    assert engine.find_profile("路人甲") is None
    print("✓ 3. 成员外号与多别名智能检索测试通过")


def test_explicit_commands(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    # Pattern 1: 🐟包 记在小本本上 [某人] 其实 [喜好/事实]
    c1 = engine.parse_explicit_command("🐟包 记在小本本上 criss 其实最喜欢🐻男猛男了")
    assert c1["is_command"] is True
    assert c1["target"] == "criss"
    assert "最喜欢🐻男猛男了" in c1["fact"]

    # Pattern 2: 小本本记录 [某人] [事实]
    c2 = engine.parse_explicit_command("小本本记录 时月原来超级喜欢玩三国杀")
    assert c2["is_command"] is True
    assert c2["target"] == "时月"
    assert "三国杀" in c2["fact"]

    # Pattern 3: 记在小本本上 [内容]
    c3 = engine.parse_explicit_command("记在小本本上 孩董其实不仅变态还想女装")
    assert c3["is_command"] is True
    assert c3["target"] == "孩董"
    assert "女装" in c3["fact"]

    # Pattern 4: 给 [某人] 记上一笔
    c4 = engine.parse_explicit_command("小鱼包在小本本上写上 给老王记上一笔：天天摸鱼打卡")
    assert c4["is_command"] is True
    assert c4["target"] == "老王"
    assert "天天摸鱼打卡" in c4["fact"]

    # Negative patterns
    assert engine.parse_explicit_command("今天天气真不错啊")["is_command"] is False
    assert engine.parse_explicit_command("谁有那个小本子看下")["is_command"] is False
    assert engine.parse_explicit_command("记在心上")["is_command"] is False

    # Command Execution
    res = engine.handle_explicit_command("123456", "测试发言人", "group_100", "🐟包 记在小本本上 孩董其实最爱看肌肉男了")
    assert res is not None
    assert res["target"] == "孩董"
    assert "肌肉男" in res["fact"]
    assert "记在小本本上了" in res["ack_hint"]

    # Check criss profile updated
    criss = engine.find_profile("孩董")
    mems = json.loads(criss["key_memories"])
    assert any("最爱看肌肉男" in m for m in mems)
    print("✓ 4. 显式小本本指令解析与执行测试通过")


def test_interaction_extraction(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    # Direct interaction with first-person declaration
    prof = engine.extract_interaction("999888", "群友阿强", "group_100", "我平时最喜欢玩黑神话悟空了", is_direct=True)
    assert prof is not None
    assert prof["primary_name"] == "群友阿强"
    interests = json.loads(prof["interests"])
    assert any("黑神话" in i for i in interests)

    # Bind actual QQ number to seed profile if speaker is 孩董
    prof_criss = engine.extract_interaction("777666", "孩董", "group_100", "我才不是变态呢", is_direct=True)
    assert prof_criss["primary_name"] == "criss"
    assert "777666" in json.loads(prof_criss["aliases"])
    # Can now find by QQ number
    found_by_qq = engine.find_profile("777666")
    assert found_by_qq is not None
    assert found_by_qq["primary_name"] == "criss"
    print("✓ 5. 直接互动捎带提取与 QQ 号动态绑定测试通过")


def test_idle_digest(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    messages = [
        {"sender_id": "111", "sender": "群友小明", "content": "草 乐 难绷 启动！"},
        {"sender_id": "222", "sender": "群友小华", "content": "原神启动！今天蒸了吗？"},
        {"sender_id": "333", "sender": "群友小李", "content": "🐟包 记在小本本上 时月天天在群里装纯"},
    ]

    res = engine.digest_idle_batch("group_100", messages)
    assert res["digested_count"] == 3
    assert "时月" in res["updated_members"]

    # Verify that explicit command in idle batch was processed
    shiyue = engine.find_profile("时月")
    mems = json.loads(shiyue["key_memories"])
    assert any("装纯" in m for m in mems)
    print("✓ 6. 空闲后台轻量摘要与习惯提取测试通过")


def test_l1_context_generation(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    # Generate L1 compact card for criss and 时月
    cards = engine.build_l1_context("group_100", user_names=["孩董", "时月"])
    assert len(cards) == 2, f"Expected 2 cards, got {len(cards)}"

    for card in cards:
        assert card.startswith("【群友档案：")
        assert "外号:" in card
        assert "风格:" in card
        assert "XP喜好:" in card
        assert "核心记忆:" in card
        # Compactness check: must be concise (~50-150 characters, ~80 tokens)
        assert len(card) < 250, f"Card too long ({len(card)} chars): {card}"

    print("✓ 7. Tier 1 (L1 Context) 超精简画像卡片生成测试通过 (~80 tokens)")


def test_l2_dossier_and_boundary_rules(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    # Record private event in group_A
    criss_prof = engine.find_profile("孩董")
    assert criss_prof is not None
    uid = criss_prof["user_id"]

    # Record private event in group_A
    engine.record_event(
        user_id=uid,
        group_id="group_A",
        quote="谁有大胸猛男照片私我",
        context="在群A索要猛男照",
        source="interaction",
        tags=["猛男"],
    )

    # Record private event in group_B
    engine.record_event(
        user_id=uid,
        group_id="group_B",
        quote="今天群B好无聊",
        context="在群B抱怨无聊",
        source="interaction",
        tags=["日常"],
    )

    # Query dossier scoped to group_A
    dossier_a = engine.query_member_dossier("孩董", group_id="group_A")
    assert dossier_a["found"] is True
    # Shared personality profile
    assert dossier_a["profile"]["primary_name"] == "criss"
    assert "妃姬杯" in dossier_a["profile"]["aliases"]
    # Group-scoped private event isolation
    events_a = dossier_a["recent_events"]
    assert len(events_a) == 1
    assert events_a[0]["group_id"] == "group_A"
    assert "群A" in events_a[0]["context"]

    # Query dossier scoped to group_B
    dossier_b = engine.query_member_dossier("孩董", group_id="group_B")
    events_b = dossier_b["recent_events"]
    assert len(events_b) == 1
    assert events_b[0]["group_id"] == "group_B"
    assert "群B" in events_b[0]["context"]

    # Query without group_id returns all events across groups
    dossier_all = engine.query_member_dossier("孩董")
    assert len(dossier_all["recent_events"]) >= 2
    all_groups = {e["group_id"] for e in dossier_all["recent_events"]}
    assert "group_A" in all_groups and "group_B" in all_groups

    print("✓ 8. Tier 2 (L2 DB) query_member_dossier 与群聊私密事件边界隔离测试通过")


def test_mcp_integration():
    # 1. Check CHAT_TOOLS includes query_member_dossier
    assert "query_member_dossier" in CHAT_TOOLS, "query_member_dossier must be in CHAT_TOOLS"

    # 2. Check schemas includes query_member_dossier with valid schema
    from chatlocal.agent_tools import tool
    schemas = chat_schemas(tool)
    dossier_tool = next((s for s in schemas if s["name"] == "query_member_dossier"), None)
    assert dossier_tool is not None, "query_member_dossier schema must exist"
    assert "identifier" in dossier_tool["parameters"]["properties"]
    assert "group_id" in dossier_tool["parameters"]["properties"]

    # 3. Check compact_wait retains member_dossiers
    sample_packet = {
        "event": "messages",
        "chat_guidance": {
            "prompt_version": "v1",
            "persona_name": "🐟包",
            "member_dossiers": ["【群友档案：孩董】外号: 妃姬杯 | XP: 🐻男"],
            "social_context": {},
        },
    }
    compacted = compact_wait(sample_packet)
    assert "member_dossiers" in compacted["chat_guidance"]
    assert compacted["chat_guidance"]["member_dossiers"] == ["【群友档案：孩董】外号: 妃姬杯 | XP: 🐻男"]
    print("✓ 9. MCP 协议端工具声明、参数校验与 compact_wait 交付测试通过")


def main():
    print("========================================================================")
    print("        [测试] Tulpa 群友动态小本本记忆 & 专属调侃系统验证")
    print("========================================================================")
    with tempfile.TemporaryDirectory(dir=str(ROOT / ".tmp"), prefix="mem-check-") as tmp:
        tmp_dir = Path(tmp)
        test_schema_and_init(tmp_dir)
        test_seeding(tmp_dir)
        test_alias_matching(tmp_dir)
        test_explicit_commands(tmp_dir)
        test_interaction_extraction(tmp_dir)
        test_idle_digest(tmp_dir)
        test_l1_context_generation(tmp_dir)
        test_l2_dossier_and_boundary_rules(tmp_dir)
        test_mcp_integration()
    print("========================================================================")
    print("✓ 全部 9 项核心测试均通过！")
    print("========================================================================")


if __name__ == "__main__":
    main()
