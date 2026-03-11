# main.py — that one bird 🐦
# Entry point: loads all cogs and starts the bot

import sys
import os
import asyncio
import aiosqlite
from datetime import datetime, timezone

import discord
from discord.ext import commands
from dotenv import load_dotenv

print("Python version:", sys.version)
load_dotenv()

TOKEN  = os.getenv("TOKEN")
PREFIX = os.getenv("PREFIX", "?")   # fallback; overridden per-guild from DB

DB = "bot.db"

# ── Callable prefix (per-guild, from DB) ──────────────────────
async def get_prefix(bot, message):
    if not message.guild:
        return PREFIX
    prefix = bot.prefix_cache.get(message.guild.id)
    if prefix is None:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT prefix FROM guild_settings WHERE guild_id=?",
                (message.guild.id,)
            ) as cur:
                row = await cur.fetchone()
        prefix = row[0] if (row and row[0]) else PREFIX
        bot.prefix_cache[message.guild.id] = prefix
    return prefix

# ── DB init ───────────────────────────────────────────────────
async def init_db():
    async with aiosqlite.connect(DB) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id              INTEGER PRIMARY KEY,
                prefix                TEXT    DEFAULT '?',
                log_mod_id            INTEGER,
                log_message_id        INTEGER,
                log_member_id         INTEGER,
                log_server_id         INTEGER,
                deadchat_role_id      INTEGER,
                deadchat_perm_role    INTEGER,
                autorole_id           INTEGER,
                welcome_channel_id    INTEGER,
                welcome_message       TEXT,
                jail_channel_id       INTEGER,
                jail_role_id          INTEGER,
                starboard_channel_id  INTEGER,
                starboard_emoji       TEXT    DEFAULT '⭐',
                starboard_threshold   INTEGER DEFAULT 3,
                chapter_channel_id    INTEGER,
                chapter_role_id       INTEGER,
                character_channel_id  INTEGER,
                antiraid_enabled      INTEGER DEFAULT 0,
                antiraid_threshold    INTEGER DEFAULT 10,
                antiraid_seconds      INTEGER DEFAULT 10,
                antiraid_action       TEXT    DEFAULT 'slowmode',
                automod_enabled       INTEGER DEFAULT 1,
                automod_action        TEXT    DEFAULT 'delete_only',
                automod_mute_minutes  INTEGER DEFAULT 10,
                automod_warn_expiry   TEXT
            );
            CREATE TABLE IF NOT EXISTS warns (
                warn_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER,
                guild_id      INTEGER,
                moderator_id  INTEGER,
                reason        TEXT,
                expires_at    TEXT,
                timestamp     TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mod_logs (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id      INTEGER,
                action        TEXT,
                user_id       INTEGER,
                moderator_id  INTEGER,
                reason        TEXT,
                timestamp     TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id  INTEGER,
                command   TEXT,
                seconds   INTEGER,
                PRIMARY KEY (guild_id, command)
            );
            CREATE TABLE IF NOT EXISTS triggers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id    INTEGER,
                trigger     TEXT,
                response    TEXT,
                match_type  TEXT DEFAULT 'contains'
            );
            CREATE TABLE IF NOT EXISTS custom_commands (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id     INTEGER,
                name         TEXT,
                action_type  TEXT,
                value        TEXT,
                UNIQUE(guild_id, name)
            );
            CREATE TABLE IF NOT EXISTS reminders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                channel_id  INTEGER,
                message     TEXT,
                remind_at   TEXT
            );
            CREATE TABLE IF NOT EXISTS automod_words (
                guild_id  INTEGER,
                word      TEXT,
                PRIMARY KEY (guild_id, word)
            );
            CREATE TABLE IF NOT EXISTS jailed_roles (
                user_id   INTEGER,
                guild_id  INTEGER,
                role_id   INTEGER
            );
            CREATE TABLE IF NOT EXISTS starboard_posted (
                guild_id    INTEGER,
                message_id  INTEGER,
                PRIMARY KEY (guild_id, message_id)
            );
            CREATE TABLE IF NOT EXISTS afk (
                user_id   INTEGER,
                guild_id  INTEGER,
                reason    TEXT,
                timestamp TEXT,
                PRIMARY KEY (user_id, guild_id)
            );
            CREATE TABLE IF NOT EXISTS tempbans (
                user_id   INTEGER,
                guild_id  INTEGER,
                unban_at  TEXT,
                PRIMARY KEY (user_id, guild_id)
            );
            CREATE TABLE IF NOT EXISTS command_perms (
                guild_id  INTEGER,
                command   TEXT,
                role_id   INTEGER,
                silent    INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, command)
            );
            CREATE TABLE IF NOT EXISTS announced_chapters (
                guild_id        INTEGER,
                chapter_number  INTEGER,
                PRIMARY KEY (guild_id, chapter_number)
            );
            CREATE TABLE IF NOT EXISTS announced_characters (
                guild_id  INTEGER,
                char_name TEXT,
                PRIMARY KEY (guild_id, char_name)
            );
        """)
        await db.commit()

# ── Bot setup ─────────────────────────────────────────────────
intents = discord.Intents.all()

class Bird(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=get_prefix, intents=intents, help_command=None)
        self.prefix_cache: dict[int, str] = {}
        self.join_tracker: dict[int, list] = {}   # guild_id -> [timestamps]

    async def setup_hook(self):
        await init_db()
        cogs = [
            "cogs.settings",
            "cogs.moderation",
            "cogs.fun",
            "cogs.info",
            "cogs.automod",
            "cogs.triggers",
            "cogs.modlog",
            "cogs.bloodtrials",
        ]
        for cog in cogs:
            try:
                await self.load_extension(cog)
                print(f"  ✓ {cog}")
            except Exception as e:
                print(f"  ✗ {cog}: {e}")

    async def on_ready(self):
        print(f"\nLogged in as {self.user} (ID: {self.user.id})")
        print("that one bird 🐦 is ready!\n")
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} slash command(s)")
        except Exception as e:
            print("Sync error:", e)

bot = Bird()

if __name__ == "__main__":
    bot.run(TOKEN)
