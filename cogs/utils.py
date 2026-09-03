# cogs/utils.py — shared pure helpers (no listeners, no cog)
import re
import asyncio
import aiosqlite
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands

DB = "bot.db"

DEFAULT_COOLDOWNS = {
    "deadchat": 3600,
    "meme":     10,
    "roast":    15,
    "8ball":    5,
    "poll":     30,
    "urban":    10,
}

# In-memory stores
cooldown_tracker: dict[tuple, datetime]   = {}
snipe_cache:      dict[int, list[dict]]   = defaultdict(list)

# ── Duration ──────────────────────────────────────────────────
def parse_duration(text: str) -> Optional[timedelta]:
    if not text:
        return None
    m = re.fullmatch(r'(\d+)([dhm])', text.strip().lower())
    if not m:
        return None
    n, u = int(m.group(1)), m.group(2)
    return timedelta(days=n) if u=='d' else timedelta(hours=n) if u=='h' else timedelta(minutes=n)

def is_url(text: str) -> bool:
    return bool(re.match(r'https?://', text.strip()))

def fmt_duration(td: timedelta) -> str:
    s = int(td.total_seconds())
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    parts = []
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if s: parts.append(f"{s}s")
    return " ".join(parts) or "0s"

# ── Settings ──────────────────────────────────────────────────
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
            INSERT INTO guild_settings (guild_id, {key}) VALUES (?,?)
            ON CONFLICT(guild_id) DO UPDATE SET {key}=excluded.{key}
        """, (guild_id, value))
        await db.commit()

# ── Response display mode ─────────────────────────────────────
async def get_display_mode(guild_id: int, command: str) -> tuple[str, int]:
    """Returns (mode, seconds). mode: public | ephemeral | timed"""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT mode, seconds FROM command_display WHERE guild_id=? AND command=?",
            (guild_id, command)
        ) as cur:
            row = await cur.fetchone()
    return (row[0], row[1]) if row else ("public", 5)

async def smart_send(ctx_or_interaction, content=None, embed=None, command: str = None, **kwargs):
    """Send with the configured display mode for this command."""
    guild_id = None
    if isinstance(ctx_or_interaction, discord.Interaction):
        guild_id = ctx_or_interaction.guild_id
    elif hasattr(ctx_or_interaction, 'guild') and ctx_or_interaction.guild:
        guild_id = ctx_or_interaction.guild.id

    mode, seconds = ("public", 5)
    if guild_id and command:
        mode, seconds = await get_display_mode(guild_id, command)

    if isinstance(ctx_or_interaction, discord.Interaction):
        i = ctx_or_interaction
        send = i.followup.send if i.response.is_done() else i.response.send_message
        ep = (mode == "ephemeral")
        msg = await send(content=content, embed=embed, ephemeral=ep, **kwargs)
        if mode == "timed" and not ep:
            try:
                m = await i.original_response()
                await asyncio.sleep(seconds)
                await m.delete()
            except Exception:
                pass
    else:
        ctx = ctx_or_interaction
        msg = await ctx.send(content=content, embed=embed, **kwargs)
        if mode == "timed":
            await asyncio.sleep(seconds)
            try:
                await msg.delete()
            except Exception:
                pass
    return msg

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
    key = (guild_id, command, user_id)
    last = cooldown_tracker.get(key)
    secs = await get_cooldown_seconds(guild_id, command)
    if last:
        elapsed = (datetime.now(timezone.utc) - last).total_seconds()
        if elapsed < secs:
            return secs - elapsed
    return 0.0

def set_cooldown_ts(guild_id: int, user_id: int, command: str):
    cooldown_tracker[(guild_id, command, user_id)] = datetime.now(timezone.utc)

# ── Mod log ───────────────────────────────────────────────────
async def log_action(bot, action: str, user, moderator,
                     reason: str = None, guild_id: int = None, proof_url: str = None):
    gid = guild_id or (user.guild.id if hasattr(user, 'guild') else None)
    if not gid:
        return
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO mod_logs (guild_id,action,user_id,moderator_id,reason,proof_url)"
            " VALUES (?,?,?,?,?,?)",
            (gid, action, user.id, moderator.id, reason, proof_url)
        )
        await db.commit()
    log_id = await get_setting(gid, 'log_mod_id')
    if not log_id:
        return
    ch = bot.get_channel(log_id)
    if not ch:
        return
    e = discord.Embed(title=f"🔨 {action}", color=0xFF4444,
                      timestamp=datetime.now(timezone.utc))
    e.add_field(name="User",   value=f"{user} ({user.mention})")
    e.add_field(name="By",     value=moderator.mention)
    e.add_field(name="Reason", value=reason or "None", inline=False)
    if proof_url:
        e.add_field(name="📎 Proof", value=f"[Jump to message]({proof_url})", inline=False)
    try:
        await ch.send(embed=e)
    except discord.Forbidden:
        pass

# ── Warns ─────────────────────────────────────────────────────
async def get_warn_count(uid: int, gid: int) -> int:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM warns WHERE user_id=? AND guild_id=?"
            " AND (expires_at IS NULL OR expires_at > ?)",
            (uid, gid, now)
        ) as cur:
            row = await cur.fetchone()
    return row[0] if row else 0

async def get_all_warns(uid: int, gid: int) -> list:
    now = datetime.now(timezone.utc).isoformat()
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT warn_id,moderator_id,reason,proof_url,expires_at,timestamp FROM warns"
            " WHERE user_id=? AND guild_id=? AND (expires_at IS NULL OR expires_at > ?)"
            " ORDER BY timestamp DESC",
            (uid, gid, now)
        ) as cur:
            return await cur.fetchall()

async def add_warn(uid: int, gid: int, mod_id: int, reason: str,
                   expires_at=None, proof_url: str = None) -> int:
    async with aiosqlite.connect(DB) as db:
        await db.execute(
            "INSERT INTO warns (user_id,guild_id,moderator_id,reason,proof_url,expires_at)"
            " VALUES (?,?,?,?,?,?)",
            (uid, gid, mod_id, reason,
             proof_url,
             expires_at.isoformat() if expires_at else None)
        )
        await db.commit()
    return await get_warn_count(uid, gid)

async def remove_warn_by_id(warn_id: int, gid: int) -> bool:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "DELETE FROM warns WHERE warn_id=? AND guild_id=?", (warn_id, gid))
        await db.commit()
    return cur.rowcount > 0

async def clear_all_warns(uid: int, gid: int) -> int:
    async with aiosqlite.connect(DB) as db:
        cur = await db.execute(
            "DELETE FROM warns WHERE user_id=? AND guild_id=?", (uid, gid))
        await db.commit()
    return cur.rowcount

# ── DM ────────────────────────────────────────────────────────
async def try_dm(user, embed: discord.Embed):
    try:
        await user.send(embed=embed)
    except (discord.Forbidden, discord.HTTPException):
        pass

# ── Fetch member ──────────────────────────────────────────────
async def fetch_member(bot, guild_id: int, member_id: int) -> Optional[discord.Member]:
    guild = bot.get_guild(guild_id) or await bot.fetch_guild(guild_id)
    try:
        return guild.get_member(member_id) or await guild.fetch_member(member_id)
    except discord.NotFound:
        return None

# ── Proof from reply ──────────────────────────────────────────
async def get_proof(ctx: commands.Context) -> Optional[tuple[str, str]]:
    """Returns (jump_url, preview_text) or None."""
    if not ctx.message.reference:
        return None
    try:
        ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        preview = ref.content[:120] + ("..." if len(ref.content) > 120 else "")
        return ref.jump_url, preview
    except Exception:
        return None

async def get_reply_target(ctx: commands.Context) -> Optional[discord.Member]:
    """If the command message is a reply, return the replied-to member."""
    if not ctx.message.reference:
        return None
    try:
        ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        return ref.author if isinstance(ref.author, discord.Member) else None
    except Exception:
        return None

# ── Command permission check ──────────────────────────────────
async def check_cmd_perm(guild_id: int, user: discord.Member,
                         command: str) -> tuple[bool, bool]:
    """Returns (allowed, silent)."""
    async with aiosqlite.connect(DB) as db:
        async with db.execute(
            "SELECT role_id, silent FROM command_perms WHERE guild_id=? AND command=?",
            (guild_id, command)
        ) as cur:
            row = await cur.fetchone()
    if not row:
        return True, False
    role_id, silent = row
    if role_id == 0:
        return True, False
    if user.guild_permissions.administrator:
        return True, False
    return any(r.id == role_id for r in user.roles), bool(silent)
