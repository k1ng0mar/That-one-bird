# main.py — that one bird 🐦
# Entry point: loads all cogs and starts the bot + Flask keep-alive for Render

import sys
import os
import asyncio
import aiosqlite
from datetime import datetime, timezone
import threading  # For background Flask thread

# ── Flask keep-alive for Render Web Service (binds to $PORT) ──
from flask import Flask

flask_app = Flask(__name__)

@flask_app.route('/')
def home():
    return "that one bird is alive! 🐦"

def run_flask():
    port = int(os.environ.get("PORT", 8080))  # Render provides PORT env var
    flask_app.run(
        host='0.0.0.0',          # Must be 0.0.0.0 for external access
        port=port,
        debug=False,
        use_reloader=False       # Avoid double-run in dev
    )

# Start Flask in background BEFORE bot starts
threading.Thread(target=run_flask, daemon=True).start()

# ── Your original imports and code below ──────────────────────
import discord
from discord.ext import commands
from dotenv import load_dotenv

print("Python:", sys.version)
load_dotenv()

TOKEN  = os.getenv("TOKEN")
PREFIX = os.getenv("PREFIX", "?")
DB     = "bot.db"

# ── Per-guild prefix ──────────────────────────────────────────
async def get_prefix(bot, message):
    if not message.guild:
        return PREFIX
    p = bot.prefix_cache.get(message.guild.id)
    if p is None:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT prefix FROM guild_settings WHERE guild_id=?",
                (message.guild.id,)
            ) as cur:
                row = await cur.fetchone()
        p = (row[0] if row and row[0] else PREFIX)
        bot.prefix_cache[message.guild.id] = p
    return p

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
                automod_warn_expiry   TEXT,
                warn_kick_threshold   INTEGER DEFAULT 3,
                warn_ban_threshold    INTEGER DEFAULT 0,
                warn_mute_threshold   INTEGER DEFAULT 0,
                warn_mute_minutes     INTEGER DEFAULT 10
            );
            CREATE TABLE IF NOT EXISTS warns (
                warn_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER,
                guild_id      INTEGER,
                moderator_id  INTEGER,
                reason        TEXT,
                proof_url     TEXT,
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
                proof_url     TEXT,
                timestamp     TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS cooldowns (
                guild_id  INTEGER,
                command   TEXT,
                seconds   INTEGER,
                PRIMARY KEY (guild_id, command)
            );
            CREATE TABLE IF NOT EXISTS command_display (
                guild_id  INTEGER,
                command   TEXT,
                mode      TEXT DEFAULT 'public',
                seconds   INTEGER DEFAULT 5,
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
            CREATE TABLE IF NOT EXISTS bookmarks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER,
                guild_id    INTEGER,
                message_id  INTEGER,
                channel_id  INTEGER,
                jump_url    TEXT,
                content     TEXT,
                author_name TEXT,
                timestamp   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS mute_tracking (
                user_id        INTEGER,
                guild_id       INTEGER,
                unmute_at      TEXT,
                notified       INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, guild_id)
            );
        """)
        await db.commit()

# ── Bot ───────────────────────────────────────────────────────
intents = discord.Intents.all()

class Bird(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix=get_prefix, intents=intents, help_command=None)
        self.prefix_cache:  dict[int, str]   = {}
        self.join_tracker:  dict[int, list]  = {}

    async def setup_hook(self):
        await init_db()
        cogs = [
            "cogs.utils_cog",
            "cogs.settings",
            "cogs.moderation",
            "cogs.roles",
            "cogs.fun",
            "cogs.info",
            "cogs.automod",
            "cogs.triggers",
            "cogs.events",
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
            print(f"Synced {len(synced)} slash commands")
        except Exception as e:
            print("Sync error:", e)

    async def on_tree_error(self, interaction: discord.Interaction,
                            error: discord.app_commands.AppCommandError):
        msg = "❌ Something went wrong."
        if isinstance(error, discord.app_commands.MissingPermissions):
            msg = "❌ You don't have permission to use this command."
        elif isinstance(error, discord.app_commands.BotMissingPermissions):
            msg = "❌ I'm missing permissions to do that."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

bot = Bird()

if __name__ == "__main__":
    bot.run(TOKEN)
