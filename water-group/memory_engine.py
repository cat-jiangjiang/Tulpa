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
import re
import sqlite3
import threading
import time
from pathlib import Path

from .memory_commands import known_names, parse_command
from .memory_format import normalize_list, render_l1_card

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

    def sync_member_mappings(self, mappings, group_id=None):
        """Binds explicit member mappings (QQ -> identity/aliases/traits/seeds).
        mappings: dict of { "qq_or_id": { "name": ..., "aliases": [...], "role": ..., "tone_style": ..., "interests": [...], "desc": ..., "seed_key": ... } }
        """
        if not mappings or not isinstance(mappings, dict):
            return 0

        synced = 0
        now = time.time()
        with self.lock, self._connect() as conn:
            for target_id, info in mappings.items():
                if not isinstance(info, dict):
                    continue
                target_id = str(target_id).strip()
                if not target_id:
                    continue
                name = str(info.get("name") or target_id).strip()
                aliases = [str(a).strip() for a in (info.get("aliases") or []) if str(a).strip()]
                role = str(info.get("role") or "").strip()
                tone_style = str(info.get("tone_style") or "").strip()
                interests = [str(i).strip() for i in (info.get("interests") or []) if str(i).strip()]
                desc = str(info.get("desc") or "").strip()
                seed_key = str(info.get("seed_key") or "").strip()
                card = str(info.get("card") or "").strip()

                # Check if seed_key or candidate seed row exists
                seed_row = None
                if seed_key:
                    seed_row = conn.execute("SELECT * FROM member_profiles WHERE user_id=?", (seed_key,)).fetchone()
                if not seed_row:
                    seed_row = conn.execute(
                        "SELECT * FROM member_profiles WHERE LOWER(primary_name)=LOWER(?) AND user_id!=? AND user_id LIKE 'seed:%'",
                        (name, target_id),
                    ).fetchone()

                seed_aliases = json.loads(seed_row["aliases"] or "[]") if seed_row else []
                seed_interests = json.loads(seed_row["interests"] or "[]") if seed_row else []
                seed_traits = json.loads(seed_row["traits"] or "{}") if seed_row else {}
                seed_memories = json.loads(seed_row["key_memories"] or "[]") if seed_row else []
                seed_tone = seed_row["tone_style"] if seed_row else ""
                seed_id = seed_row["user_id"] if seed_row else None

                existing_target = conn.execute("SELECT * FROM member_profiles WHERE user_id=?", (target_id,)).fetchone()
                cur_aliases = json.loads(existing_target["aliases"] or "[]") if existing_target else []
                cur_interests = json.loads(existing_target["interests"] or "[]") if existing_target else []
                cur_traits = json.loads(existing_target["traits"] or "{}") if existing_target else {}
                cur_memories = json.loads(existing_target["key_memories"] or "[]") if existing_target else []

                merged_aliases = list(dict.fromkeys([name, target_id] + ([card] if card else []) + aliases + seed_aliases + cur_aliases))
                merged_interests = list(dict.fromkeys(interests + seed_interests + cur_interests))
                merged_traits = {**seed_traits, **cur_traits}
                if role:
                    merged_traits["role"] = role
                if card:
                    merged_traits["card"] = card
                merged_tone = tone_style or seed_tone or (existing_target["tone_style"] if existing_target and existing_target["tone_style"] else "普通群友")

                desc_list = [desc] if desc and desc not in seed_memories and desc not in cur_memories else []
                merged_memories = list(dict.fromkeys(desc_list + seed_memories + cur_memories))[:5]

                if existing_target:
                    conn.execute(
                        """
                        UPDATE member_profiles
                        SET primary_name=?, aliases=?, tone_style=?, interests=?, traits=?, key_memories=?, updated_at=?
                        WHERE user_id=?
                        """,
                        (
                            name,
                            json.dumps(merged_aliases, ensure_ascii=False),
                            merged_tone,
                            json.dumps(merged_interests, ensure_ascii=False),
                            json.dumps(merged_traits, ensure_ascii=False),
                            json.dumps(merged_memories, ensure_ascii=False),
                            now,
                            target_id,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        INSERT INTO member_profiles (user_id, primary_name, aliases, tone_style, interests, traits, key_memories, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target_id,
                            name,
                            json.dumps(merged_aliases, ensure_ascii=False),
                            merged_tone,
                            json.dumps(merged_interests, ensure_ascii=False),
                            json.dumps(merged_traits, ensure_ascii=False),
                            json.dumps(merged_memories, ensure_ascii=False),
                            now,
                            now,
                        ),
                    )

                # Migrate events from seed to target QQ and remove retired seed row
                if seed_id and seed_id != target_id:
                    conn.execute("UPDATE member_events SET user_id=? WHERE user_id=?", (target_id, seed_id))
                    conn.execute("DELETE FROM member_profiles WHERE user_id=?", (seed_id,))

                synced += 1

        return synced

    def sync_onebot_members(self, members, group_id=None):
        """Syncs member list from OneBot, recording/updating cards and nicknames into profiles."""
        if not members:
            return 0
        updated = 0
        now = time.time()
        with self.lock, self._connect() as conn:
            for m in members:
                if not isinstance(m, dict):
                    continue
                uid = str(m.get("user_id") or "").strip()
                if not uid or not uid.isdigit():
                    continue
                nickname = str(m.get("nickname") or "").strip()
                card = str(m.get("card") or "").strip()
                role = str(m.get("role") or "").strip()

                prof = conn.execute("SELECT * FROM member_profiles WHERE user_id=?", (uid,)).fetchone()
                if prof:
                    aliases = json.loads(prof["aliases"] or "[]")
                    traits = json.loads(prof["traits"] or "{}")
                    changed = False
                    for item in (card, nickname):
                        if item and item not in aliases:
                            aliases.append(item)
                            changed = True
                    if card and traits.get("card") != card:
                        traits["card"] = card
                        changed = True
                    if nickname and traits.get("nickname") != nickname:
                        traits["nickname"] = nickname
                        changed = True
                    if role in ("owner", "admin") and traits.get("group_role") != role:
                        traits["group_role"] = role
                        changed = True
                    if changed:
                        conn.execute(
                            "UPDATE member_profiles SET aliases=?, traits=?, updated_at=? WHERE user_id=?",
                            (json.dumps(aliases, ensure_ascii=False), json.dumps(traits, ensure_ascii=False), now, uid),
                        )
                        updated += 1
                else:
                    # Create lightweight profile for newly observed group member
                    p_name = card or nickname or uid
                    aliases = [a for a in [p_name, uid, card, nickname] if a]
                    aliases = list(dict.fromkeys(aliases))
                    traits = {"card": card, "nickname": nickname}
                    if role in ("owner", "admin"):
                        traits["group_role"] = role
                    conn.execute(
                        """
                        INSERT INTO member_profiles (user_id, primary_name, aliases, tone_style, interests, traits, key_memories, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (uid, p_name, json.dumps(aliases, ensure_ascii=False), "普通群友", "[]", json.dumps(traits, ensure_ascii=False), "[]", now, now),
                    )
                    updated += 1
        return updated

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
        """Recognises user banter commands like:

        - 🐟包 记在小本本上 [某人] 其实[喜好/事实]
        - @bot 记在小本本上 [某人] 喜欢[某事物]
        - 小本本记录 [内容]
        - 记在小本本上 [某人] [喜好/事实]
        - 快记在小本本上 [某人] [喜好/事实]

        The grammar lives in memory_commands; this method only supplies the
        profile lookups it needs.
        """
        return parse_command(text, self._known_command_names, self._primary_name)

    def _known_command_names(self):
        with self.lock, self._connect() as conn:
            rows = conn.execute("SELECT primary_name, aliases FROM member_profiles").fetchall()
        return known_names(rows)

    def _primary_name(self, candidate):
        prof = self.find_profile(candidate)
        return prof["primary_name"] if prof else None

    def handle_explicit_command(self, sender_id, sender_name, group_id, text):
        """Processes an explicit banter command, updating DB and compact profiles."""
        cmd = self.parse_explicit_command(text)
        if not cmd.get("is_command"):
            return None

        target = cmd.get("target")
        fact = cmd.get("fact")
        content = cmd.get("content")

        # If target is a QQ number, check if this is an identity annotation (e.g. "是 时月", "其实是时月", "时月 (地牢主)")
        if target and str(target).isdigit():
            ident_m = re.match(
                r"^(?:其实(?:是|叫)|原来(?:是|叫)|是|叫|为)?\s*(?P<name>[\w\u4e00-\u9fa5]{2,12})(?:\s*[（\(](?P<role>[^）\)]+)[）\)])?(?:\s*(?:其实|平时|特征|特点|喜欢|爱)?\s*(?P<desc>.+))?$",
                fact,
            )
            if ident_m and ident_m.group("name") not in ("喜欢", "讨厌", "经常", "天天", "特别"):
                p_name = ident_m.group("name").strip()
                p_role = (ident_m.group("role") or "").strip()
                p_desc = (ident_m.group("desc") or "").strip()

                mapping_entry = {
                    "name": p_name,
                    "aliases": [p_name],
                    "role": p_role,
                    "desc": p_desc or fact,
                }
                seed_cand = self.find_profile(p_name)
                if seed_cand and seed_cand["user_id"].startswith("seed:"):
                    mapping_entry["seed_key"] = seed_cand["user_id"]

                self.sync_member_mappings({target: mapping_entry}, group_id=group_id)
                ack_hint = f"认准了！已把 QQ {target} 标注为【{p_name}】{f'（{p_role}）' if p_role else ''}，小本本已锁定，以后发言精准怼他！"

                ev_id = self.record_event(
                    user_id=target,
                    group_id=group_id,
                    quote=text,
                    context=f"标注身份: {p_name} {p_role} {p_desc}".strip(),
                    source="explicit_annotation",
                    tags=["标注", "explicit_annotation"],
                )
                return {
                    "event_id": ev_id,
                    "target": p_name,
                    "fact": fact or content,
                    "user_id": target,
                    "ack_hint": ack_hint,
                }

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
            if not isinstance(m, dict) or m.get("is_self") or m.get("synthetic"):
                continue
            sender_id = str(m.get("sender_id") or m.get("user_id") or "").strip()
            if not sender_id or sender_id == "0":
                continue

            sender_name = str(
                m.get("sender_name")
                or m.get("nickname")
                or (m.get("sender", {}).get("nickname") if isinstance(m.get("sender"), dict) else m.get("sender"))
                or ""
            ).strip()
            text = str(m.get("content") or m.get("text") or m.get("message") or "").strip()
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

            # Check for latest group-scoped event note if any
            group_event_note = ""
            if group_id:
                with self.lock, self._connect() as conn:
                    ev = conn.execute(
                        "SELECT context, quote FROM member_events WHERE user_id=? AND group_id=? ORDER BY timestamp DESC LIMIT 1",
                        (uid, str(group_id)),
                    ).fetchone()
                    if ev:
                        group_event_note = (ev["context"] or ev["quote"]).strip()

            cards.append(render_l1_card(prof, group_event_note))

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

    @staticmethod
    def _normalize_list(val):
        """Normalizes string or list/iterable inputs into a deduplicated list of non-empty strings."""
        return normalize_list(val)

    def update_member_dossier(
        self,
        identifier,
        key_memories=None,
        traits=None,
        aliases=None,
        interests=None,
        tone_style=None,
        role=None,
        primary_name=None,
        overwrite=False,
    ):
        """Tier 2 (L2 DB Storage) update for MCP tool `update_member_dossier`.

        Allows external agents to natively update a member's dossier (key memories,
        traits, aliases, roles, interests, tone style) without touching raw SQLite.
        """
        if not identifier or not str(identifier).strip():
            return {"success": False, "error": "identifier_required", "note": "必须提供群友标识（QQ号、昵称、名片或既有外号）。"}

        ident_str = str(identifier).strip()
        prof = self.find_profile(ident_str)

        norm_aliases = self._normalize_list(aliases)
        norm_interests = self._normalize_list(interests)
        norm_memories = self._normalize_list(key_memories)
        norm_traits = dict(traits) if isinstance(traits, dict) else ({} if traits is not None else None)

        now = time.time()
        with self.lock, self._connect() as conn:
            if prof:
                uid = prof["user_id"]
                p_name = primary_name.strip() if (primary_name and primary_name.strip()) else prof["primary_name"]

                cur_aliases = json.loads(prof["aliases"] or "[]")
                cur_interests = json.loads(prof["interests"] or "[]")
                cur_traits = json.loads(prof["traits"] or "{}")
                cur_memories = json.loads(prof["key_memories"] or "[]")

                if overwrite:
                    new_aliases = norm_aliases if norm_aliases is not None else cur_aliases
                    new_interests = norm_interests if norm_interests is not None else cur_interests
                    new_traits = norm_traits if norm_traits is not None else cur_traits
                    new_memories = (norm_memories if norm_memories is not None else cur_memories)[:10]
                    new_tone = tone_style.strip() if (tone_style is not None and str(tone_style).strip()) else (prof["tone_style"] if tone_style is None else "")
                else:
                    new_aliases = list(dict.fromkeys(cur_aliases + (norm_aliases or [])))
                    new_interests = list(dict.fromkeys(cur_interests + (norm_interests or [])))
                    new_traits = {**cur_traits, **(norm_traits or {})}
                    new_memories = list(dict.fromkeys(cur_memories + (norm_memories or [])))[:10]
                    new_tone = tone_style.strip() if (tone_style and str(tone_style).strip()) else prof["tone_style"]

                if role:
                    new_traits["role"] = str(role).strip()

                if p_name not in new_aliases:
                    new_aliases.insert(0, p_name)

                conn.execute(
                    """
                    UPDATE member_profiles
                    SET primary_name=?, aliases=?, tone_style=?, interests=?, traits=?, key_memories=?, updated_at=?
                    WHERE user_id=?
                    """,
                    (
                        p_name,
                        json.dumps(new_aliases, ensure_ascii=False),
                        new_tone,
                        json.dumps(new_interests, ensure_ascii=False),
                        json.dumps(new_traits, ensure_ascii=False),
                        json.dumps(new_memories, ensure_ascii=False),
                        now,
                        uid,
                    ),
                )
            else:
                uid = ident_str
                p_name = primary_name.strip() if (primary_name and primary_name.strip()) else ident_str
                new_aliases = list(dict.fromkeys([p_name, ident_str] + (norm_aliases or [])))
                new_interests = list(dict.fromkeys(norm_interests or []))
                new_traits = dict(norm_traits or {})
                if role:
                    new_traits["role"] = str(role).strip()
                new_memories = (norm_memories or [])[:10]
                new_tone = tone_style.strip() if (tone_style and str(tone_style).strip()) else "普通群友"

                conn.execute(
                    """
                    INSERT INTO member_profiles (user_id, primary_name, aliases, tone_style, interests, traits, key_memories, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        uid,
                        p_name,
                        json.dumps(new_aliases, ensure_ascii=False),
                        new_tone,
                        json.dumps(new_interests, ensure_ascii=False),
                        json.dumps(new_traits, ensure_ascii=False),
                        json.dumps(new_memories, ensure_ascii=False),
                        now,
                        now,
                    ),
                )

        updated_prof = self.find_profile(uid)
        return {
            "success": True,
            "user_id": uid,
            "primary_name": updated_prof["primary_name"],
            "profile": {
                "user_id": updated_prof["user_id"],
                "primary_name": updated_prof["primary_name"],
                "aliases": json.loads(updated_prof["aliases"] or "[]"),
                "tone_style": updated_prof["tone_style"],
                "interests": json.loads(updated_prof["interests"] or "[]"),
                "traits": json.loads(updated_prof["traits"] or "{}"),
                "key_memories": json.loads(updated_prof["key_memories"] or "[]"),
                "updated_at": updated_prof["updated_at"],
            },
            "note": f"已成功更新群友【{updated_prof['primary_name']}】的小本本画像档案。",
        }

    def consolidate_chat_memory(
        self,
        group_id=None,
        session_id=None,
        records=None,
        events=None,
        messages=None,
        summary=None,
    ):
        """Batch consolidates recent chat insights and events into structured dossiers.

        Allows external agents to summarize recent chat batch events, update
        multiple members' profiles and record gossips/events in one call.
        """
        updated_profiles = []
        recorded_events_count = 0
        gid = str(group_id).strip() if group_id else ""

        # 1. Process records (member profile updates + optional scoped event)
        if records and isinstance(records, list):
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                ident = rec.get("identifier") or rec.get("user_id") or rec.get("name")
                if not ident:
                    continue
                res = self.update_member_dossier(
                    identifier=ident,
                    key_memories=rec.get("key_memories"),
                    traits=rec.get("traits"),
                    aliases=rec.get("aliases"),
                    interests=rec.get("interests"),
                    tone_style=rec.get("tone_style"),
                    role=rec.get("role"),
                    primary_name=rec.get("primary_name"),
                    overwrite=bool(rec.get("overwrite", False)),
                )
                if res.get("success"):
                    updated_profiles.append(res["primary_name"])
                    ev = rec.get("event")
                    ev_gid = str(ev.get("group_id") or gid).strip() if isinstance(ev, dict) else ""
                    if isinstance(ev, dict) and ev_gid:
                        self.record_event(
                            user_id=res["user_id"],
                            group_id=ev_gid,
                            quote=ev.get("quote", ""),
                            context=ev.get("context", ""),
                            source=ev.get("source", "consolidation"),
                            tags=ev.get("tags", []),
                        )
                        recorded_events_count += 1

        # 2. Process independent events
        if events and isinstance(events, list):
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ident = ev.get("identifier") or ev.get("user_id")
                if not ident:
                    continue
                ev_gid = str(ev.get("group_id") or gid).strip()
                if not ev_gid:
                    continue
                prof = self.find_profile(ident)
                uid = prof["user_id"] if prof else str(ident).strip()
                self.record_event(
                    user_id=uid,
                    group_id=ev_gid,
                    quote=ev.get("quote", ""),
                    context=ev.get("context", ""),
                    source=ev.get("source", "consolidation"),
                    tags=ev.get("tags", []),
                )
                recorded_events_count += 1

        # 3. Process raw messages digest if provided
        digested_count = 0
        if messages and isinstance(messages, list):
            digest_res = self.digest_idle_batch(gid, messages)
            digested_count = digest_res.get("digested_count", 0)
            for m_name in digest_res.get("updated_members", []):
                if m_name not in updated_profiles:
                    updated_profiles.append(m_name)

        # 4. If summary is provided and group_id is present, record a group-level gossip milestone
        if summary and str(summary).strip() and gid:
            self.record_event(
                user_id="group_summary",
                group_id=gid,
                quote=str(summary).strip()[:500],
                context="话题阶段性提炼归档",
                source="summary",
                tags=["话题总结", "群聊纪要"],
            )
            recorded_events_count += 1

        updated_unique = list(dict.fromkeys(updated_profiles))
        return {
            "success": True,
            "group_id": gid or None,
            "updated_profiles_count": len(updated_unique),
            "updated_members": updated_unique,
            "recorded_events_count": recorded_events_count,
            "digested_messages_count": digested_count,
            "note": f"成功归档：已提炼更新 {len(updated_unique)} 位群友档案，沉淀 {recorded_events_count} 条事件记忆。",
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
