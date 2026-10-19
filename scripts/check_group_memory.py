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
    with engine._connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert str(mode).lower() == "wal", f"Expected WAL journal mode, got {mode}"
    print("✓ 1. SQLite 架构初始化与建表及 WAL 模式测试通过")


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

    # Anti-Hijacking and negative safety tests:
    # 1. Single character queries must NEVER match multi-character profiles
    assert engine.find_profile("c") is None, "Single character 'c' must not match 'criss'"
    assert engine.find_profile("月") is None, "Single character '月' must not match '时月'"
    # 2. Sentences/phrases containing alias must not match
    assert engine.find_profile("领导发奖金") is None, "'领导发奖金' must not match 时月"
    # 3. Nonexistent
    assert engine.find_profile("路人甲") is None
    print("✓ 3. 成员外号与多别名智能检索及防劫持测试通过")


def test_explicit_commands(tmp_dir):
    db_file = tmp_dir / "test_memories.sqlite3"
    engine = MemoryEngine(db_file)
    engine.seed_from_persona()

    # Pattern 1: 🐟包 记在小本本上 [某人] 其实 [喜好/事实]
    c1 = engine.parse_explicit_command("🐟包 记在小本本上 criss 其实最喜欢🐻男猛男了")
    assert c1["is_command"] is True
    assert c1["target"] == "criss"
    assert "最喜欢🐻男猛男了" in c1["fact"]

    # Pattern 2: Verb preservation: "孩董喜欢🐻男" -> fact must preserve "喜欢" (not just "🐻男"!)
    c2 = engine.parse_explicit_command("🐟包 记在小本本上 孩董喜欢🐻男")
    assert c2["is_command"] is True
    assert c2["target"] == "criss"
    assert "喜欢🐻男" in c2["fact"]

    # Pattern 3: @bot invocation
    c3 = engine.parse_explicit_command("@123456 记在小本本上 孩董 其实喜欢健身猛男")
    assert c3["is_command"] is True
    assert c3["target"] == "criss"
    assert "喜欢健身猛男" in c3["fact"]

    # Pattern 4: Punctuation after bot name
    c4 = engine.parse_explicit_command("🐟包，记在小本本上 孩董其实爱女装")
    assert c4["is_command"] is True
    assert c4["target"] == "criss"
    assert "爱女装" in c4["fact"]

    c4b = engine.parse_explicit_command("鱼包: 记在小本本上 孩董喜欢猛男")
    assert c4b["is_command"] is True
    assert c4b["target"] == "criss"
    assert "喜欢猛男" in c4b["fact"]

    # Pattern 5: Conversational prefixes (快记在 / 帮我记在 / 记小本本)
    c5 = engine.parse_explicit_command("快记在小本本上 孩董喜欢猛男")
    assert c5["is_command"] is True
    assert c5["target"] == "criss"

    c6 = engine.parse_explicit_command("帮我记在小本本上 时月爱玩原神")
    assert c6["is_command"] is True
    assert c6["target"] == "时月"
    assert "爱玩原神" in c6["fact"]

    c7 = engine.parse_explicit_command("记小本本上 时月是个大网黄")
    assert c7["is_command"] is True
    assert c7["target"] == "时月"
    assert "大网黄" in c7["fact"]

    # Pattern 8: Colon delimiter
    c8 = engine.parse_explicit_command("小鱼包在小本本上写上 给老王记上一笔：天天摸鱼打卡")
    assert c8["is_command"] is True
    assert c8["target"] == "老王"
    assert "天天摸鱼打卡" in c8["fact"]

    # Negative patterns
    assert engine.parse_explicit_command("今天天气真不错啊")["is_command"] is False
    assert engine.parse_explicit_command("谁有那个小本子看下")["is_command"] is False
    assert engine.parse_explicit_command("记在心上")["is_command"] is False

    # Command Execution: verify predicate preservation in fact, memory, and ack_hint
    res = engine.handle_explicit_command("123456", "测试发言人", "group_100", "🐟包 记在小本本上 孩董喜欢猛男")
    assert res is not None
    assert res["target"] == "criss"
    assert res["fact"] == "喜欢猛男"
    assert "记在小本本上了！这就给 criss 记上一笔：喜欢猛男" in res["ack_hint"]

    # Check criss profile updated
    criss = engine.find_profile("孩董")
    mems = json.loads(criss["key_memories"])
    assert any("喜欢猛男" in m for m in mems)
    print("✓ 4. 显式小本本指令解析与执行测试通过 (含真实@提及、谓词完整性保留与标点支持)")


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

    # ANTI-HIJACKING SAFETY TEST:
    # A random stranger with nickname "月" or "领导发奖金" must NOT hijack seed:shiyue!
    impostor_prof = engine.extract_interaction("555444", "月", "group_100", "今天真开心", is_direct=False)
    assert impostor_prof["primary_name"] == "月"
    assert impostor_prof["user_id"] == "555444"
    # Verify seed:shiyue is untouched
    shiyue_check = engine.find_profile("时月")
    assert shiyue_check["user_id"] == "seed:shiyue", "seed:shiyue must NOT be hijacked by user named '月'"

    # Pre-record an event under seed:criss to test event migration on legitimate binding
    engine.record_event("seed:criss", "group_100", "孩董黑历史", "被群友调侃", "seed_data")

    # Legitimate binding: speaker with exact alias "孩董" binds to seed:criss
    prof_criss = engine.extract_interaction("777666", "孩董", "group_100", "我才不是变态呢", is_direct=True)
    assert prof_criss["primary_name"] == "criss"
    assert "777666" in json.loads(prof_criss["aliases"])
    # Can now find by QQ number
    found_by_qq = engine.find_profile("777666")
    assert found_by_qq is not None
    assert found_by_qq["primary_name"] == "criss"
    # Verify events were migrated to real QQ
    dossier = engine.query_member_dossier("777666", "group_100")
    assert any("孩董黑历史" in e["quote"] for e in dossier["recent_events"])
    print("✓ 5. 直接互动捎带提取、QQ号动态绑定与防冒名劫持测试通过")


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

    # DEDUPLICATION CHECK:
    # Run idle digest a second time on the same window:
    # Events must NOT be duplicated in member_events table!
    with engine._connect() as conn:
        count_before = conn.execute("SELECT COUNT(*) FROM member_events").fetchone()[0]

    engine.digest_idle_batch("group_100", messages)

    with engine._connect() as conn:
        count_after = conn.execute("SELECT COUNT(*) FROM member_events").fetchone()[0]

    assert count_after == count_before, f"Events must not duplicate across idle runs! Before: {count_before}, After: {count_after}"
    print("✓ 6. 空闲后台轻量摘要与多轮重复调用防冗余去重测试通过")


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


def test_native_dossier_updates_and_consolidation(tmp_dir):
    db_path = tmp_dir / "test_native_updates.sqlite3"
    engine = MemoryEngine(db_path)
    engine.seed_from_persona()

    # 1. Test update_member_dossier for existing profile (时月)
    update_res = engine.update_member_dossier(
        identifier="时月",
        key_memories=["经常在深夜偷玩三国杀并爆金币"],
        role="至尊地牢主",
        interests=["蒸蒸日上", "反差网黄"],
    )
    assert update_res["success"] is True
    assert update_res["primary_name"] == "时月"
    prof = engine.find_profile("时月")
    memories = json.loads(prof["key_memories"])
    traits = json.loads(prof["traits"])
    interests = json.loads(prof["interests"])
    assert any("经常在深夜偷玩三国杀并爆金币" in m for m in memories)
    assert traits.get("role") == "至尊地牢主"
    assert "蒸蒸日上" in interests

    # 2. Test update_member_dossier for a brand new member
    new_res = engine.update_member_dossier(
        identifier="999888",
        primary_name="新群友老王",
        aliases=["老王", "隔壁老王"],
        key_memories=["喜欢研究AI大模型架构"],
        role="技术架构师",
        tone_style="严谨技术宅",
    )
    assert new_res["success"] is True
    new_prof = engine.find_profile("999888")
    assert new_prof is not None
    assert new_prof["primary_name"] == "新群友老王"
    assert "隔壁老王" in json.loads(new_prof["aliases"])
    assert new_prof["tone_style"] == "严谨技术宅"

    # 3. Test consolidate_chat_memory in batch
    con_res = engine.consolidate_chat_memory(
        group_id="group_test_888",
        records=[
            {
                "identifier": "999888",
                "key_memories": ["新加入Tulpa项目讨论"],
                "event": {
                    "quote": "大家晚上好，今天聊聊MCP",
                    "context": "首次在群内技术发言",
                    "tags": ["技术交流"],
                },
            }
        ],
        events=[
            {
                "identifier": "时月",
                "quote": "原神启动！",
                "context": "深夜地牢摸鱼",
                "tags": ["原神", "摸鱼"],
            }
        ],
        summary="群内热烈探讨了MCP协议与记忆归档方案",
    )
    assert con_res["success"] is True
    assert con_res["updated_profiles_count"] >= 1
    assert con_res["recorded_events_count"] == 3  # 1 record event + 1 independent event + 1 summary

    # 4. Edge-case test: string arguments for list fields, overwrite clearing, and per-event group_id
    str_arg_res = engine.update_member_dossier(
        identifier="999888",
        aliases="技术达人",  # String instead of list
        interests="大语言模型",  # String instead of list
        key_memories="单条核心记忆测试",  # String instead of list
    )
    assert str_arg_res["success"] is True
    assert "技术达人" in str_arg_res["profile"]["aliases"]
    assert "大语言模型" in str_arg_res["profile"]["interests"]
    assert "单条核心记忆测试" in str_arg_res["profile"]["key_memories"]
    # Ensure string wasn't split character-by-character into ['单', '条', '核', ...]
    assert "单" not in str_arg_res["profile"]["key_memories"]

    # Test explicit overwrite clearing
    clear_res = engine.update_member_dossier(
        identifier="999888",
        key_memories=[],
        overwrite=True,
    )
    assert clear_res["success"] is True
    assert clear_res["profile"]["key_memories"] == [], "key_memories should be cleared in overwrite mode with empty list"

    # Test consolidate_chat_memory with per-event group_id
    con_scoped_res = engine.consolidate_chat_memory(
        events=[
            {
                "identifier": "999888",
                "group_id": "group_scoped_777",
                "quote": "测试按事件划分群组",
                "context": "独立群事件",
            }
        ]
    )
    assert con_scoped_res["recorded_events_count"] == 1
    dossier_scoped = engine.query_member_dossier("999888", group_id="group_scoped_777")
    assert any("测试按事件划分群组" in e["quote"] for e in dossier_scoped["recent_events"])

    print("✓ 9. MCP 原生档案更新 update_member_dossier 与批量记忆归档 consolidate_chat_memory 测试通过")


def test_mcp_integration():
    # 1. Check CHAT_TOOLS includes query_member_dossier, update_member_dossier, consolidate_chat_memory
    assert "query_member_dossier" in CHAT_TOOLS, "query_member_dossier must be in CHAT_TOOLS"
    assert "update_member_dossier" in CHAT_TOOLS, "update_member_dossier must be in CHAT_TOOLS"
    assert "consolidate_chat_memory" in CHAT_TOOLS, "consolidate_chat_memory must be in CHAT_TOOLS"

    # 2. Check schemas includes dossiers tools with valid schemas
    from chatlocal.agent_tools import tool
    schemas = chat_schemas(tool)
    dossier_tool = next((s for s in schemas if s["name"] == "query_member_dossier"), None)
    assert dossier_tool is not None, "query_member_dossier schema must exist"
    assert "identifier" in dossier_tool["parameters"]["properties"]
    assert "group_id" in dossier_tool["parameters"]["properties"]

    update_tool = next((s for s in schemas if s["name"] == "update_member_dossier"), None)
    assert update_tool is not None, "update_member_dossier schema must exist"
    assert "identifier" in update_tool["parameters"]["properties"]
    assert "key_memories" in update_tool["parameters"]["properties"]
    assert "traits" in update_tool["parameters"]["properties"]

    consolidate_tool = next((s for s in schemas if s["name"] == "consolidate_chat_memory"), None)
    assert consolidate_tool is not None, "consolidate_chat_memory schema must exist"
    assert "records" in consolidate_tool["parameters"]["properties"]
    assert "events" in consolidate_tool["parameters"]["properties"]
    assert "summary" in consolidate_tool["parameters"]["properties"]

    # 3. Check compact_wait retains member_dossiers AND injects prompt guidance into reminders
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
    assert any("小本本画像" in r for r in compacted["chat_guidance"]["reminders"]), "compact_wait must deliver dynamic memory reminder to Agent"
    print("✓ 10. MCP 协议端工具声明、参数校验与 compact_wait 交付及提示词注入测试通过")


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
        test_native_dossier_updates_and_consolidation(tmp_dir)
        test_mcp_integration()
    print("========================================================================")
    print("✓ 全部 10 项核心测试均通过！")
    print("========================================================================")


if __name__ == "__main__":
    main()
