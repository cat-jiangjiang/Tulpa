"""Group member dynamic memory & personalized targeted reply system.

Two-Tier Storage:
- Tier 1 (L1 Context): Compact synthesized profiles (aliases, tone style, XP preferences, 3-5 key memories)
  automatically injected into Agent's context when members speak (~80 tokens per relevant member).
- Tier 2 (L2 DB Storage): Persistent in SQLite (data/group_memories.sqlite3) with timeline event logs
  (timestamp, quote, context) and structured slot cards (interests, jargons, relationships). Exposed via
  MCP tool `query_member_dossier`.

Boundary Rules:
- Cross-group shared personality/XP profiles (same QQ account shares profile across groups).
- Group-scoped private event timelines and gossip (events recorded in group A are filtered by group_id).
"""
import json
from contextlib import contextmanager
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "group_memories.sqlite3"


class MemoryEngine:
    """Manages persistent SQLite storage, seeding, command parsing, and context building."""

    def __init__(self, db_path=None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self._init_db()

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            with conn:
                yield conn
        finally:
            conn.close()


    def _init_db(self):
        with self.lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS member_profiles (
                    user_id TEXT PRIMARY KEY,
                    primary_name TEXT NOT NULL,
                    aliases TEXT NOT NULL DEFAULT '[]',
                    tone_style TEXT NOT NULL DEFAULT '',
                    interests TEXT NOT NULL DEFAULT '[]',
                    traits TEXT NOT NULL DEFAULT '{}',
                    key_memories TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profiles_name ON member_profiles(primary_name);

                CREATE TABLE IF NOT EXISTS member_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    group_id TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    quote TEXT NOT NULL DEFAULT '',
                    context TEXT NOT NULL DEFAULT '',
                    source TEXT NOT NULL DEFAULT 'interaction',
                    tags TEXT NOT NULL DEFAULT '[]',
                    created_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_user_group ON member_events(user_id, group_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_events_group ON member_events(group_id, timestamp DESC);
                CREATE INDEX IF NOT EXISTS idx_events_user ON member_events(user_id, timestamp DESC);
                """
            )

    def get_member_count(self):
        with self.lock, self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM member_profiles").fetchone()
            return row[0] if row else 0

    def seed_from_persona(self, persona_path=None):
        """Seeds initial member profiles from yu_bao.md or persona markdown."""
        path = Path(persona_path) if persona_path else PROJECT_ROOT / "water-group" / "personas" / "yu_bao.md"
        if not path.exists():
            return 0

        now = time.time()
        seeds = [
            {
                "user_id": "seed:shiyue",
                "primary_name": "时月",
                "aliases": ["时月", "🐶领导", "地牢主", "地牢🐷", "领导"],
                "tone_style": "爱装纯、嘴硬、假正经真网黄反差",
                "interests": ["原神", "三国杀", "大网黄反差", "地牢"],
                "traits": {"trope": "伪清水真网黄", "games": ["原神", "三国杀"], "role": "地牢主"},
                "key_memories": [
                    "嘴上一直嘴硬自称清水很清纯，实际上玩得超级大是个大网黄",
                    "重度喜欢玩原神和三国杀（蒸蒸日上）",
                    "被群友和鱼包称为🐶领导、地牢主、地牢🐷",
                ],
            },
            {
                "user_id": "seed:criss",
                "primary_name": "criss",
                "aliases": ["criss", "孩董", "妃姬杯"],
                "tone_style": "变态嘴硬、爱搞黄、说话嘴欠",
                "interests": ["🐻男", "胸肌猛男", "大胸男", "变态XP"],
                "traits": {"trope": "变态🐻男控", "xp": "狂热喜欢🐻男/大胸猛男", "role": "孩董"},
                "key_memories": [
                    "群里的孩董，外号妃姬杯，XP极其变态狂热喜欢🐻男/大胸猛男",
                    "看到肌肉男大胸男走不动道",
                    "常被群友和鱼包精准吐槽收收味",
                ],
            },
        ]

        inserted = 0
        with self.lock, self._connect() as conn:
            for s in seeds:
                # Check if profile already exists by user_id or primary_name
                row = conn.execute(
                    "SELECT user_id, aliases, interests, key_memories, traits FROM member_profiles WHERE user_id=? OR primary_name=?",
                    (s["user_id"], s["primary_name"]),
                ).fetchone()

                if not row:
                    conn.execute(
                        """
                        INSERT INTO member_profiles (user_id, primary_name, aliases, tone_style, interests, traits, key_memories, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s["user_id"],
                            s["primary_name"],
                            json.dumps(s["aliases"], ensure_ascii=False),
                            s["tone_style"],
                            json.dumps(s["interests"], ensure_ascii=False),
                            json.dumps(s["traits"], ensure_ascii=False),
                            json.dumps(s["key_memories"], ensure_ascii=False),
                            now,
                            now,
                        ),
                    )
                    inserted += 1
                else:
                    # Merge aliases & interests if missing
                    existing_aliases = json.loads(row["aliases"] or "[]")
                    merged_aliases = list(dict.fromkeys(existing_aliases + s["aliases"]))
                    existing_interests = json.loads(row["interests"] or "[]")
                    merged_interests = list(dict.fromkeys(existing_interests + s["interests"]))
                    existing_memories = json.loads(row["key_memories"] or "[]")
                    merged_memories = list(dict.fromkeys(existing_memories + s["key_memories"]))[:5]

                    conn.execute(
                        """
                        UPDATE member_profiles SET aliases=?, interests=?, key_memories=?, updated_at=?
                        WHERE user_id=?
                        """,
                        (
                            json.dumps(merged_aliases, ensure_ascii=False),
                            json.dumps(merged_interests, ensure_ascii=False),
                            json.dumps(merged_memories, ensure_ascii=False),
                            now,
                            row["user_id"],
                        ),
                    )
        return inserted

    def find_profile(self, identifier):
        """Finds a member profile by user_id, primary_name, or alias."""
        if not identifier:
            return None
        ident_str = str(identifier).strip()
        if not ident_str:
            return None

        with self.lock, self._connect() as conn:
            # 1. Exact match on user_id
            row = conn.execute("SELECT * FROM member_profiles WHERE user_id=?", (ident_str,)).fetchone()
            if row:
                return dict(row)

            # 2. Exact match on primary_name (case-insensitive)
            row = conn.execute("SELECT * FROM member_profiles WHERE LOWER(primary_name)=LOWER(?)", (ident_str,)).fetchone()
            if row:
                return dict(row)

            # 3. Match within aliases JSON array (exact case-insensitive match)
            all_rows = conn.execute("SELECT * FROM member_profiles").fetchall()
            ident_lower = ident_str.casefold()
            for r in all_rows:
                aliases = json.loads(r["aliases"] or "[]")
                if any(ident_lower == str(a).casefold() for a in aliases):
                    return dict(r)

            # 4. Conservative substring match on primary_name or specific aliases.
            # Requires len >= 2 and query is a substring of the profile's name/alias.
            # Never checks if an alias is a substring of query (preventing "领导发奖金" matching "时月").
            if len(ident_lower) >= 2:
                for r in all_rows:
                    p_name = str(r["primary_name"]).casefold()
                    if ident_lower in p_name:
                        return dict(r)
                    aliases = json.loads(r["aliases"] or "[]")
                    for a in aliases:
                        a_str = str(a).casefold()
                        if a_str in ("领导", "群友"):
                            continue
                        if ident_lower in a_str:
                            return dict(r)

        return None

    def save_profile(self, user_id, primary_name, aliases=None, tone_style="", interests=None, traits=None, key_memories=None):
        """Creates or updates a member profile (cross-group shared)."""
        now = time.time()
        user_id = str(user_id).strip()
        primary_name = str(primary_name).strip() or user_id

        aliases_list = list(dict.fromkeys(aliases or []))
        if primary_name not in aliases_list:
            aliases_list.append(primary_name)
        interests_list = list(dict.fromkeys(interests or []))
        traits_dict = traits or {}
        memories_list = list(dict.fromkeys(key_memories or []))[:5]

        with self.lock, self._connect() as conn:
            existing = conn.execute("SELECT * FROM member_profiles WHERE user_id=?", (user_id,)).fetchone()
            if existing:
                cur_aliases = json.loads(existing["aliases"] or "[]")
                cur_interests = json.loads(existing["interests"] or "[]")
                cur_traits = json.loads(existing["traits"] or "{}")
                cur_memories = json.loads(existing["key_memories"] or "[]")

                merged_aliases = list(dict.fromkeys(cur_aliases + aliases_list))
                merged_interests = list(dict.fromkeys(cur_interests + interests_list))
                merged_traits = {**cur_traits, **traits_dict}
                merged_memories = list(dict.fromkeys(cur_memories + memories_list))[:5]
                tone = tone_style or existing["tone_style"]

                conn.execute(
                    """
                    UPDATE member_profiles
                    SET primary_name=?, aliases=?, tone_style=?, interests=?, traits=?, key_memories=?, updated_at=?
                    WHERE user_id=?
                    """,
                    (
                        primary_name or existing["primary_name"],
                        json.dumps(merged_aliases, ensure_ascii=False),
                        tone,
                        json.dumps(merged_interests, ensure_ascii=False),
                        json.dumps(merged_traits, ensure_ascii=False),
                        json.dumps(merged_memories, ensure_ascii=False),
                        now,
                        user_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO member_profiles (user_id, primary_name, aliases, tone_style, interests, traits, key_memories, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user_id,
                        primary_name,
                        json.dumps(aliases_list, ensure_ascii=False),
                        tone_style,
                        json.dumps(interests_list, ensure_ascii=False),
                        json.dumps(traits_dict, ensure_ascii=False),
                        json.dumps(memories_list, ensure_ascii=False),
                        now,
                        now,
                    ),
                )

        return self.find_profile(user_id)

    def record_event(self, user_id, group_id, quote, context, source="interaction", tags=None, timestamp=None):
        """Records a timeline event scoped to a specific group, with recent deduplication."""
        now = time.time()
        ts = timestamp if timestamp is not None else now
        user_id = str(user_id).strip()
        group_id = str(group_id).strip()
        tags_list = list(dict.fromkeys(tags or []))
        q_str = str(quote)[:1000]
        c_str = str(context)[:1000]
        s_str = str(source)[:50]

        with self.lock, self._connect() as conn:
            # Deduplicate identical recent events within 300 seconds (e.g. idle digest loops)
            dup = conn.execute(
                """
                SELECT id FROM member_events
                WHERE user_id=? AND group_id=? AND quote=? AND source=? AND timestamp > ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id, group_id, q_str, s_str, ts - 300),
            ).fetchone()
            if dup:
                return dup[0]

            cursor = conn.execute(
                """
                INSERT INTO member_events (user_id, group_id, timestamp, quote, context, source, tags, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    group_id,
                    ts,
                    q_str,
                    c_str,
                    s_str,
                    json.dumps(tags_list, ensure_ascii=False),
                    now,
                ),
            )
            event_id = cursor.lastrowid
            conn.execute("UPDATE member_profiles SET updated_at=? WHERE user_id=?", (now, user_id))
            return event_id

    def parse_explicit_command(self, text):
        """Recognizes user banter commands like:

        - 🐟包 记在小本本上 [某人] 其实[喜好/事实]
        - @bot 记在小本本上 [某人] 喜欢[某事物]
        - 小本本记录 [内容]
        - 记在小本本上 [某人] [喜好/事实]
        - 快记在小本本上 [某人] [喜好/事实]
        """
        if not text or not isinstance(text, str):
            return {"is_command": False}

        raw = text.strip()

        # Robust regex supporting @bot, @123456, 🐟包, 鱼包, punctuation, and conversational prefixes
        bot_prefix = r"(?:(?:@\d+|@?(?:[🐟鱼小]*包[子]?|机器人|bot))\s*[，,：:!！\s]*)?"
        action_prefix = (
            r"(?:(?:请|帮我|麻烦|快)?(?:在)?小本本(?:上)?(?:记(?:录|下|上一笔|一笔|一下)?|写上)"
            r"|(?:请|帮我|麻烦|快)?(?:记在|记入|写在|写进|记)小本本(?:上)?(?:记|录|写)?"
            r"|(?:拿|用)小本本(?:记(?:录|下|上一笔|一笔|一下)?|写上))"
        )
        cmd_pattern = re.compile(
            r"^" + bot_prefix + action_prefix + r"\s*[：:，,\s]\s*(?P<content>.+)$|^" + bot_prefix + action_prefix + r"\s*(?P<content2>\S.+)$",
            re.IGNORECASE,
        )

        m = cmd_pattern.match(raw)
        if not m:
            return {"is_command": False}

        content = (m.group("content") or m.group("content2") or "").strip()
        if not content:
            return {"is_command": False}

        # 1. Match colon delimiter: 给老王记上一笔：天天摸鱼打卡 or 时月: 装纯
        colon_m = re.match(r"^(?:给\s*)?(?P<target>[\w\u4e00-\u9fa5]{2,16}?)(?:\s*(?:记上|记一笔|记上一笔))?\s*[：:]\s*(?P<fact>.+)$", content)
        if colon_m:
            target = colon_m.group("target").strip()
            fact = colon_m.group("fact").strip()
            return {"is_command": True, "target": target, "fact": fact, "content": content}

        # 2. Check if content starts with a known member profile (longest candidate first)
        with self.lock, self._connect() as conn:
            rows = conn.execute("SELECT primary_name, aliases FROM member_profiles").fetchall()
        known_names = set()
        for r in rows:
            known_names.add(r["primary_name"])
            for a in json.loads(r["aliases"] or "[]"):
                if len(str(a)) >= 2 and str(a) not in ("领导", "群友"):
                    known_names.add(str(a))

        for cand in sorted(known_names, key=len, reverse=True):
            if content.startswith(cand):
                fact_str = content[len(cand):].strip(" ：:，,")
                if fact_str:
                    prof = self.find_profile(cand)
                    target_name = prof["primary_name"] if prof else cand
                    return {
                        "is_command": True,
                        "target": target_name,
                        "fact": fact_str,
                        "content": content,
                    }

        # 3. Match target with predicate / transition words (e.g. "老王 其实天天打卡", "张三 喜欢吃螺蛳粉")
        # NOTE: The predicate words MUST be captured as part of (?P<fact>...) so verbs like "喜欢" are not lost!
        trans_m = re.match(
            r"^(?:给\s*)?(?P<target>[\w\u4e00-\u9fa5]{2,12}?)\s*(?:记上|记一笔|记上一笔)?\s*(?P<fact>(?:其实|原来|特别|非常|最|平时|经常|天天|整天|又|一直|每天|是个|是|爱|喜欢|讨厌|沉迷).+)$",
            content,
        )
        if trans_m:
            target = trans_m.group("target").strip()
            fact = trans_m.group("fact").strip()
            return {"is_command": True, "target": target, "fact": fact, "content": content}

        # 4. Fallback: space-separated "target fact"
        parts = content.split(maxsplit=1)
        if len(parts) == 2 and 2 <= len(parts[0]) <= 12:
            return {"is_command": True, "target": parts[0].strip(), "fact": parts[1].strip(), "content": content}

        return {"is_command": True, "target": None, "fact": content, "content": content}

    def handle_explicit_command(self, sender_id, sender_name, group_id, text):
        """Processes an explicit banter command, updating DB and compact profiles."""
        cmd = self.parse_explicit_command(text)
        if not cmd.get("is_command"):
            return None

        target = cmd.get("target")
        fact = cmd.get("fact")
        content = cmd.get("content")

        # Resolve target profile
        profile = None
        if target:
            profile = self.find_profile(target)
            if not profile:
                # Create profile with primary_name=target
                profile = self.save_profile(
                    user_id=f"member:{target}",
                    primary_name=target,
                    aliases=[target],
                )
        else:
            # General note or about sender
            profile = self.find_profile(sender_id) or self.save_profile(
                user_id=sender_id,
                primary_name=sender_name or sender_id,
            )
            target = profile["primary_name"]

        user_id = profile["user_id"]
        # Record timeline event
        ev_id = self.record_event(
            user_id=user_id,
            group_id=group_id,
            quote=text,
            context=fact or content,
            source="explicit_command",
            tags=["小本本", "explicit_command"],
        )

        # Update profile key_memories
        existing_memories = json.loads(profile["key_memories"] or "[]")
        new_memory = f"小本本记录: {fact or content}"
        if new_memory not in existing_memories:
            updated_memories = [new_memory] + existing_memories
            updated_memories = updated_memories[:5]
            with self.lock, self._connect() as conn:
                conn.execute(
                    "UPDATE member_profiles SET key_memories=?, updated_at=? WHERE user_id=?",
                    (json.dumps(updated_memories, ensure_ascii=False), time.time(), user_id),
                )

        # Formulate funny banter acknowledgment hint for bot
        ack_hint = f"记在小本本上了！这就给 {target} 记上一笔：{fact or content}"

        return {
            "event_id": ev_id,
            "target": target,
            "fact": fact or content,
            "user_id": user_id,
            "ack_hint": ack_hint,
        }

    def extract_interaction(self, sender_id, sender_name, group_id, text, is_direct=False):
        """Piggyback extraction on direct interactions (@bot or reply threads)."""
        if not text or not sender_id:
            return None

        # Update / bind profile with sender info
        profile = self.find_profile(sender_id)
        if not profile:
            # Check if sender_name matches an existing seed profile
            # SAFETY GUARD: Only bind if sender_name is an EXACT match to primary_name or distinct aliases
            # (never match on partial names or generic words like "领导")
            named_profile = None
            if sender_name and len(sender_name.strip()) >= 2 and sender_name.strip() not in ("领导", "群友"):
                candidate_prof = self.find_profile(sender_name.strip())
                if candidate_prof and candidate_prof["user_id"].startswith("seed:"):
                    # Verify exact match on primary_name or distinct aliases
                    p_name = candidate_prof["primary_name"].casefold()
                    c_aliases = [str(a).casefold() for a in json.loads(candidate_prof["aliases"] or "[]") if str(a) != "领导"]
                    if sender_name.strip().casefold() == p_name or sender_name.strip().casefold() in c_aliases:
                        named_profile = candidate_prof

            if named_profile:
                # Bind actual QQ number to this seed profile, and migrate member_events
                old_seed_id = named_profile["user_id"]
                with self.lock, self._connect() as conn:
                    cur_aliases = json.loads(named_profile["aliases"] or "[]")
                    if str(sender_id) not in cur_aliases:
                        cur_aliases.append(str(sender_id))
                    if sender_name and sender_name not in cur_aliases:
                        cur_aliases.append(sender_name)
                    if old_seed_id not in cur_aliases:
                        cur_aliases.append(old_seed_id)
                    conn.execute(
                        "UPDATE member_profiles SET user_id=?, aliases=?, updated_at=? WHERE user_id=?",
                        (str(sender_id), json.dumps(cur_aliases, ensure_ascii=False), time.time(), old_seed_id),
                    )
                    conn.execute(
                        "UPDATE member_events SET user_id=? WHERE user_id=?",
                        (str(sender_id), old_seed_id),
                    )
                profile = self.find_profile(sender_id)
            else:
                profile = self.save_profile(
                    user_id=sender_id,
                    primary_name=sender_name or sender_id,
                    aliases=[sender_name] if sender_name else [],
                )
        else:
            # Ensure sender_name is in aliases
            if sender_name:
                cur_aliases = json.loads(profile["aliases"] or "[]")
                if sender_name not in cur_aliases:
                    self.save_profile(user_id=profile["user_id"], primary_name=profile["primary_name"], aliases=[sender_name])

        # Piggyback preference extraction:
        # Check for first-person declarations: "我喜欢...", "我平时爱玩...", "我其实最爱..."
        extracted_interests = []
        interest_match = re.search(r"我(?:其实|平时|最|特别|经常|超)*(?:喜欢玩|爱玩|喜欢|爱|超爱|沉迷|在玩)\s*([A-Za-z0-9\u4e00-\u9fa5]{2,16}?)(?:了|呢|啊|哈|[，。！？\s]|$)", text)
        if interest_match:
            item = interest_match.group(1).strip()
            if item not in ("你", "大家", "什么", "这个", "那个", "哈哈", "呵呵", "嘿嘿", "嘻嘻", "草", "乐"):
                extracted_interests.append(item)

        # First-person trait declarations: "我才不是...", "我是..."
        extracted_traits = {}
        trait_match = re.search(r"我(?:才不是|不是|其实是|是个)\s*([A-Za-z0-9\u4e00-\u9fa5]{2,16}?)(?:了|呢|啊|哈|[，。！？\s]|$)", text)
        if trait_match:
            claim = trait_match.group(1).strip()
            if "不是" in text or "才不是" in text:
                extracted_traits["self_claim"] = f"否认{claim}/嘴硬"
            else:
                extracted_traits["self_claim"] = claim

        # Update profile if any info was extracted
        if extracted_interests or extracted_traits:
            profile = self.save_profile(
                user_id=profile["user_id"],
                primary_name=profile["primary_name"],
                interests=extracted_interests,
                traits=extracted_traits,
            )

        # Record interaction event
        if is_direct or extracted_interests or extracted_traits:
            self.record_event(
                user_id=profile["user_id"],
                group_id=group_id,
                quote=text[:200],
                context=f"直接互动: {'提取喜好 ' + ','.join(extracted_interests) if extracted_interests else '日常交流'}",
                source="direct_interaction" if is_direct else "interaction",
                tags=extracted_interests or ["互动"],
            )

        return self.find_profile(profile["user_id"])

    def digest_idle_batch(self, group_id, messages):
        """Light-weight batch digestion of recent group chat to extract member habits, tone, and preferences.

        Called during idle windows or periodically.
        """
        if not messages or not isinstance(messages, list):
            return {"digested_count": 0, "updated_members": []}

        updated = []
        for m in messages:
            if m.get("is_self") or m.get("synthetic"):
                continue
            sender_id = str(m.get("sender_id", "")).strip()
            if not sender_id or sender_id == "0":
                continue

            sender_name = str(m.get("sender", "")).strip()
            text = str(m.get("content", "")).strip()
            if not text:
                continue

            # Check if this is an explicit command
            cmd = self.parse_explicit_command(text)
            if cmd.get("is_command"):
                # Handle command with deduplication protection
                res = self.handle_explicit_command(sender_id, sender_name, group_id, text)
                if res and res["target"] not in updated:
                    updated.append(res["target"])
                continue

            # Extract any preference declarations in background messages
            self.extract_interaction(sender_id, sender_name, group_id, text, is_direct=False)

            # Tone & jargon observation: check for signature expressions
            prof = self.find_profile(sender_id)
            if not prof:
                prof = self.save_profile(user_id=sender_id, primary_name=sender_name or sender_id)

            tone_clues = []
            if any(w in text for w in ("草", "乐", "难绷", "蚌埠住了", "6", "？", "何意味", "幽默")):
                tone_clues.append("沙雕梗多")
            if any(w in text for w in ("🐻男", "猛男", "大胸", "胸肌", "腹肌")):
                tone_clues.append("猛男控/爱看肌肉")
                self.save_profile(user_id=prof["user_id"], primary_name=prof["primary_name"], interests=["🐻男/猛男"])
            if any(w in text for w in ("原神", "启动", "三国杀", "蒸", "崩铁", "鸣潮", "绝区零", "黑神话")):
                tone_clues.append("二次元/游戏党")
                self.save_profile(user_id=prof["user_id"], primary_name=prof["primary_name"], interests=["游戏"])

            if tone_clues and not prof.get("tone_style"):
                self.save_profile(
                    user_id=prof["user_id"],
                    primary_name=prof["primary_name"],
                    tone_style="、".join(tone_clues),
                )
                if prof["primary_name"] not in updated:
                    updated.append(prof["primary_name"])

        return {"digested_count": len(messages), "updated_members": updated}

    def observe_message(self, group_id, payload):
        """Unified entry point for incoming messages from MCPChat.receive."""
        if not payload or payload.get("is_self") or payload.get("synthetic"):
            return None

        sender_id = str(payload.get("sender_id", "")).strip()
        sender_name = str(payload.get("sender", "")).strip()
        text = str(payload.get("content", "")).strip()

        if not text or not sender_id:
            return None

        # Check explicit command first
        cmd = self.parse_explicit_command(text)
        if cmd.get("is_command"):
            return self.handle_explicit_command(sender_id, sender_name, group_id, text)

        # Check if direct mention or reply
        segs = payload.get("segments", [])
        is_direct = any(s.get("type") in ("at", "reply") for s in segs)

        return self.extract_interaction(sender_id, sender_name, group_id, text, is_direct=is_direct)

    def build_l1_context(self, group_id, user_ids=None, user_names=None, max_members=3):
        """Builds Tier 1 compact synthesized profiles for active speakers (~80 tokens per member).

        Injected into Agent's context when members speak.
        """
        user_ids = [str(u).strip() for u in (user_ids or []) if str(u).strip()]
        user_names = [str(n).strip() for n in (user_names or []) if str(n).strip()]

        seen_uids = set()
        cards = []

        # Find profiles for active speakers
        identifiers = user_ids + user_names
        for ident in identifiers:
            if len(cards) >= max_members:
                break
            prof = self.find_profile(ident)
            if not prof:
                continue
            uid = prof["user_id"]
            if uid in seen_uids:
                continue
            seen_uids.add(uid)

            # Compact profile rendering
            p_name = prof["primary_name"]
            aliases = json.loads(prof["aliases"] or "[]")
            alias_str = "/".join([a for a in aliases if a != p_name][:3]) or "无"
            tone = prof.get("tone_style") or "普通群友"
            interests = json.loads(prof["interests"] or "[]")
            interest_str = "、".join(interests[:4]) or "暂无明确"
            memories = json.loads(prof["key_memories"] or "[]")
            mem_str = "；".join(memories[:2]) or "暂无专属记录"

            # Check for latest group-scoped event note if any
            group_event_note = ""
            if group_id:
                with self.lock, self._connect() as conn:
                    ev = conn.execute(
                        "SELECT context, quote FROM member_events WHERE user_id=? AND group_id=? ORDER BY timestamp DESC LIMIT 1",
                        (uid, str(group_id)),
                    ).fetchone()
                    if ev:
                        raw_note = (ev["context"] or ev["quote"]).strip()
                        group_event_note = f" | 本群八卦: {raw_note[:45]}"

            card = f"【群友档案：{p_name}】外号: {alias_str} | 风格: {tone} | XP喜好: {interest_str} | 核心记忆: {mem_str}{group_event_note}"
            cards.append(card)

        return cards

    def query_member_dossier(self, identifier, group_id=None, limit=10):
        """Tier 2 (L2 DB Storage) query for MCP tool `query_member_dossier`.

        Enforces boundary rules:
        - Cross-group shared personality/XP profiles (same QQ account shares profile across groups).
        - Group-scoped private event timelines and gossip (events recorded in group A are filtered by group_id).
        """
        prof = self.find_profile(identifier)
        if not prof:
            return {
                "found": False,
                "identifier": identifier,
                "note": f"小本本上暂时没有找到群友【{identifier}】的专属档案。",
            }

        uid = prof["user_id"]
        aliases = json.loads(prof["aliases"] or "[]")
        interests = json.loads(prof["interests"] or "[]")
        traits = json.loads(prof["traits"] or "{}")
        key_memories = json.loads(prof["key_memories"] or "[]")

        # Query group-scoped private event timelines
        events = []
        with self.lock, self._connect() as conn:
            if group_id:
                # Scoped to specific group
                rows = conn.execute(
                    "SELECT id, group_id, timestamp, quote, context, source, tags FROM member_events WHERE user_id=? AND group_id=? ORDER BY timestamp DESC LIMIT ?",
                    (uid, str(group_id), limit),
                ).fetchall()
            else:
                # All events across groups if no group_id specified
                rows = conn.execute(
                    "SELECT id, group_id, timestamp, quote, context, source, tags FROM member_events WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
                    (uid, limit),
                ).fetchall()

            for r in rows:
                events.append(
                    {
                        "id": r["id"],
                        "group_id": r["group_id"],
                        "timestamp": r["timestamp"],
                        "quote": r["quote"],
                        "context": r["context"],
                        "source": r["source"],
                        "tags": json.loads(r["tags"] or "[]"),
                    }
                )

        return {
            "found": True,
            "profile": {
                "user_id": uid,
                "primary_name": prof["primary_name"],
                "aliases": aliases,
                "tone_style": prof["tone_style"],
                "interests": interests,
                "traits": traits,
                "key_memories": key_memories,
                "updated_at": prof["updated_at"],
            },
            "recent_events": events,
            "boundary_scope": f"本群专属 ({group_id})" if group_id else "全部群跨组事件",
            "reply_hint": f"调侃建议：可称呼其外号（{'/'.join(aliases[:3])}），根据其XP喜好（{'/'.join(interests[:3])}）自然插话，偶尔可用'我小本本上可记着...'翻黑料调侃。",
        }


# Global engine singleton
_ENGINE_INSTANCE = None
_ENGINE_LOCK = threading.Lock()


def get_memory_engine(db_path=None):
    global _ENGINE_INSTANCE
    with _ENGINE_LOCK:
        if _ENGINE_INSTANCE is None:
            _ENGINE_INSTANCE = MemoryEngine(db_path)
            # Auto seed on first load
            _ENGINE_INSTANCE.seed_from_persona()
        elif db_path and str(_ENGINE_INSTANCE.db_path) != str(db_path):
            _ENGINE_INSTANCE = MemoryEngine(db_path)
            _ENGINE_INSTANCE.seed_from_persona()
        return _ENGINE_INSTANCE
