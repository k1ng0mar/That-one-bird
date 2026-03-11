# cogs/utils.py — shared helpers

import re
import aiosqlite
from datetime import datetime, timedelta, timezone
from collections import defaultdict

import discord
from discord.ext import commands

DB = "bot.db"

DEFAULT_COOLDOWNS = {
    "deadchat": 3600,
    "meme":     10,
    "roast":    15,
    "8ball":    5,
    "poll":     30,
}

cooldown_tracker: dict[tuple, datetime] = {}
snipe_cache:      dict[int, list[dict]] = defaultdict(list)

# ── Duration parser ───────────────────────────────────────────
def parse_duration(text: str) -> timedelta | None:
    m = re.fullmatch(r'(\d+)([dhm])', text.strip().lower())
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return timedelta(days=n) if u == 'd' else timedelta(hours=n) if u == 'h' else timedelta(minutes=n)

def is_url(text: str) -> bool:
    return bool(re.match(r'https?://', text.strip()))

# ── Guild settings ────────────────────────────────────────────
async def get_setting(guild_id: int, key: str):
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            f"SELECT {key} FROM guild_settings WHERE guild_id=?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else None

async def set_setting(guild_id: int, key: str, value):
    async with aiosqlite.connect(DB) as db:
        await db.execute(f"""
            INSERT INTO guild_settings (guild_id, {key}) VALUES (?, ?)
            ON CONFLICT(guild_id) DO UPDATE SET {key}=excluded.{key}
        """, (guild_id, value))
        await db.commit()

# ── Cooldowns ─────────────────────────────────────────────────
async def get_cooldown_seconds(guild_id: int, command: str) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT seconds FROM cooldowns WHERE guild_id=? AND command=?",
            (guild_id, command)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else DEFAULT_COOLDOWNS.get(command, 30)

async def check_cooldown(guild_id: int, user_id: int, command: str) -> float:
    key     = (guild_id, command, user_id)
    last    = cooldown_tracker.get(key)
    seconds = await get_cooldown_seconds(guild_id, command)
    if last:
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < seconds:
            return seconds - elapsed
    return 0.0

async def set_cooldown_ts(guild_id: int, user_id: int, command: str):
    cooldown_tracker[(guild_id, command, user_id)] = datetime.now(timezone.utc)

# ── Mod logging ───────────────────────────────────────────────
async def log_action(bot, action: str, user, moderator, reason: str = None, guild_id: int = None):
    gid = guild_id or (user.guild.id if hasattr(user, 'guild') else None)
    if not gid:
        return
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO mod_logs (guild_id,action,user_id,moderator_id,reason) VALUES (?,?,?,?,?)",
            (gid, action, user.id, moderator.id, reason)
        )
        await db.commit()

    log_id = await get_setting(gid, 'log_mod_id')
    if log_id:
        ch = bot.get_channel(log_id)
        if ch:
            e = discord.Embed(title=f"🔨 {action}", color=0xFF4444,
                              timestamp=datetime.now(timezone.utc))
            e.add_field(name="User",   value=user.mention)
            e.add_field(name="By",     value=moderator.mention)
            e.add_field(name="Reason", value=reason or "None", inline=False)
            await ch.send(embed=e)

# ── Warns ─────────────────────────────────────────────────────
async def get_warn_count(uid: int, gid: int) -> int:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM warns WHERE user_id=? AND guild_id=? "
            "AND (expires_at IS NULL OR expires_at > ?)",
            (uid, gid, datetime.now(timezone.utc).isoformat())
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0

async def get_all_warns(uid: int, gid: int) -> list:
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT warn_id,moderator_id,reason,expires_at,timestamp FROM warns "
            "WHERE user_id=? AND guild_id=? AND (expires_at IS NULL OR expires_at > ?) "
            "ORDER BY timestamp DESC",
            (uid, gid, datetime.now(timezone.utc).isoformat())
        ) as cur:
            return await cur.fetchall()

async def add_warn(uid: int, gid: int, mod_id: int, reason: str, expires_at=None) -> int:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO warns (user_id,guild_id,moderator_id,reason,expires_at) VALUES (?,?,?,?,?)",
            (uid, gid, mod_id, reason, expires_at.isoformat() if expires_at else None)
        )
        await db.commit()
    return await get_warn_count(uid, gid)

async def remove_warn_by_id(warn_id: int, gid: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "DELETE FROM warns WHERE warn_id=? AND guild_id=?", (warn_id, gid)
        )
        await db.commit()
    return cur.rowcount > 0

async def clear_all_warns(uid: int, gid: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "DELETE FROM warns WHERE user_id=? AND guild_id=?", (uid, gid)
        )
        await db.commit()
    return cur.rowcount

# ── DM helper ─────────────────────────────────────────────────
async def try_dm(user, embed: discord.Embed):
    try:
        await user.send(embed=embed)
    except discord.Forbidden:
        pass

# ── Fetch member safely ───────────────────────────────────────
async def fetch_member(bot, guild_id: int, member_id: int):
    guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
    try:
        return guild.get_member(member_id) or await guild.fetch_member(member_id)
    except discord.NotFound:
        return None

# ── Command permission check ──────────────────────────────────
async def check_command_perm(bot, guild_id: int, user: discord.Member, command: str) -> tuple[bool, bool]:
    """Returns (allowed, silent). silent=True means fail quietly."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT role_id, silent FROM command_perms WHERE guild_id=? AND command=?",
            (guild_id, command)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return True, False
    role_id, silent = row
    if role_id == 0:  # everyone
        return True, False
    if user.guild_permissions.administrator:
        return True, False
    allowed = any(r.id == role_id for r in user.roles)
    return allowed, bool(silent)

# ── Proof from reply ──────────────────────────────────────────
async def get_proof(ctx: commands.Context) -> str | None:
    if ctx.message.reference:
        try:
            ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            return f"[📎 Proof]({ref.jump_url}): \"{ref.content[:100]}\""
        except Exception:
            pass
    return None
