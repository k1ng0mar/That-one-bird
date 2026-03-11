# cogs/bloodtrials.py — Blood Trials announcements via Supabase REST API
# Polls every 2 minutes for new published chapters and new characters.
# Add to .env:
#   SUPABASE_URL=https://yourproject.supabase.co
#   SUPABASE_KEY=your-anon-key

import os
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import aiohttp

from cogs.utils import DB, get_setting

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
BOOK_LINK    = "https://btnovel.netlify.app/#chapters"

def supa_headers() -> dict:
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }

class BloodTrials(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if SUPABASE_URL and SUPABASE_KEY:
            self.poll_chapters.start()
            self.poll_characters.start()
        else:
            print("  ⚠  BloodTrials: SUPABASE_URL or SUPABASE_KEY not set — polling disabled.")

    def cog_unload(self):
        self.poll_chapters.cancel()
        self.poll_characters.cancel()

    # ── Chapter polling ───────────────────────────────────────
    @tasks.loop(minutes=2)
    async def poll_chapters(self):
        try:
            url = f"{SUPABASE_URL}/rest/v1/chapters?published=eq.true&select=Chapter_number,title,excerpt,created_at&order=Chapter_number.asc"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=supa_headers()) as r:
                    if r.status != 200:
                        return
                    chapters = await r.json()

            for chapter in chapters:
                num     = chapter.get("Chapter_number")
                title   = chapter.get("title", "Untitled")
                excerpt = chapter.get("excerpt", "")
                pub_at  = chapter.get("created_at", "")

                # Check all guilds that have a chapter channel configured
                async with aiosqlite.connect(DB) as db:
                    async with db.execute(
                        "SELECT guild_id, chapter_channel_id, chapter_role_id "
                        "FROM guild_settings WHERE chapter_channel_id IS NOT NULL"
                    ) as cur:
                        guilds = await cur.fetchall()

                for guild_id, ch_id, role_id in guilds:
                    # Already announced?
                    async with aiosqlite.connect(DB) as db:
                        async with db.execute(
                            "SELECT 1 FROM announced_chapters WHERE guild_id=? AND chapter_number=?",
                            (guild_id, num)
                        ) as cur:
                            if await cur.fetchone():
                                continue
                        await db.execute(
                            "INSERT OR IGNORE INTO announced_chapters (guild_id, chapter_number) VALUES (?,?)",
                            (guild_id, num))
                        await db.commit()

                    ch = self.bot.get_channel(ch_id)
                    if not ch:
                        continue

                    e = discord.Embed(
                        title=f"📖 Chapter {num}: {title}",
                        description=excerpt[:1024] if excerpt else "*No excerpt available.*",
                        color=0xB22222,
                        url=BOOK_LINK,
                        timestamp=datetime.now(timezone.utc)
                    )
                    e.set_author(name="Blood Trials",
                                 icon_url="https://btnovel.netlify.app/favicon.ico")
                    e.add_field(name="📚 Read now", value=f"[Click here]({BOOK_LINK})")
                    if pub_at:
                        try:
                            dt = datetime.fromisoformat(pub_at.replace("Z", "+00:00"))
                            e.set_footer(text=f"Published {dt.strftime('%B %d, %Y')}")
                        except Exception:
                            pass

                    content = ""
                    if role_id:
                        content = f"<@&{role_id}> "
                    content += f"**A new chapter of Blood Trials just dropped!** 🩸"
                    await ch.send(content=content, embed=e,
                                  allowed_mentions=discord.AllowedMentions.all())

        except Exception as ex:
            print(f"Chapter poll error: {ex}")

    # ── Character polling ─────────────────────────────────────
    @tasks.loop(minutes=2)
    async def poll_characters(self):
        try:
            url = f"{SUPABASE_URL}/rest/v1/characters?select=name,role,description&order=name.asc"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=supa_headers()) as r:
                    if r.status != 200:
                        return
                    characters = await r.json()

            for char in characters:
                name        = char.get("name", "Unknown")
                role        = char.get("role", "")
                description = char.get("description", "")

                async with aiosqlite.connect(DB) as db:
                    async with db.execute(
                        "SELECT guild_id, character_channel_id "
                        "FROM guild_settings WHERE character_channel_id IS NOT NULL"
                    ) as cur:
                        guilds = await cur.fetchall()

                for guild_id, ch_id in guilds:
                    async with aiosqlite.connect(DB) as db:
                        async with db.execute(
                            "SELECT 1 FROM announced_characters WHERE guild_id=? AND char_name=?",
                            (guild_id, name)
                        ) as cur:
                            if await cur.fetchone():
                                continue
                        await db.execute(
                            "INSERT OR IGNORE INTO announced_characters (guild_id, char_name) VALUES (?,?)",
                            (guild_id, name))
                        await db.commit()

                    ch = self.bot.get_channel(ch_id)
                    if not ch:
                        continue

                    e = discord.Embed(
                        title=f"🧬 New Character: {name}",
                        description=description[:1024] if description else "*No description yet.*",
                        color=0x8B0000,
                        timestamp=datetime.now(timezone.utc)
                    )
                    e.set_author(name="Blood Trials — Characters")
                    if role:
                        e.add_field(name="Role", value=role)
                    e.add_field(name="📚 Read the story", value=f"[Blood Trials]({BOOK_LINK})")
                    await ch.send(
                        content="🩸 **A new character has been added to Blood Trials!**",
                        embed=e)

        except Exception as ex:
            print(f"Character poll error: {ex}")

    # ── /character lookup ─────────────────────────────────────
    @app_commands.command(name="character", description="Look up a Blood Trials character")
    @app_commands.describe(name="Character name")
    async def character(self, interaction: discord.Interaction, name: str):
        await interaction.response.defer()
        try:
            url = (f"{SUPABASE_URL}/rest/v1/characters"
                   f"?name=ilike.{name.replace(' ', '%20')}"
                   f"&select=name,role,description&limit=1")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=supa_headers()) as r:
                    if r.status != 200:
                        await interaction.followup.send("❌ Couldn't reach the database.")
                        return
                    results = await r.json()

            if not results:
                await interaction.followup.send(
                    f"❌ No character named **{name}** found.")
                return

            char = results[0]
            e = discord.Embed(
                title=f"🧬 {char['name']}",
                description=char.get('description', '*No description.*'),
                color=0x8B0000
            )
            if char.get('role'):
                e.add_field(name="Role", value=char['role'])
            e.add_field(name="📚 Read the story", value=f"[Blood Trials]({BOOK_LINK})")
            e.set_footer(text="Blood Trials")
            await interaction.followup.send(embed=e)

        except Exception as ex:
            print(f"Character lookup error: {ex}")
            await interaction.followup.send("❌ Something went wrong.")

    @commands.command(name="character")
    async def prefix_character(self, ctx: commands.Context, *, name: str):
        try:
            url = (f"{SUPABASE_URL}/rest/v1/characters"
                   f"?name=ilike.{name.replace(' ', '%20')}"
                   f"&select=name,role,description&limit=1")
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=supa_headers()) as r:
                    results = await r.json() if r.status == 200 else []

            if not results:
                await ctx.send(f"❌ No character named **{name}** found.")
                return

            char = results[0]
            e = discord.Embed(
                title=f"🧬 {char['name']}",
                description=char.get('description', '*No description.*'),
                color=0x8B0000
            )
            if char.get('role'):
                e.add_field(name="Role", value=char['role'])
            e.add_field(name="📚 Read the story", value=f"[Blood Trials]({BOOK_LINK})")
            await ctx.send(embed=e)
        except Exception as ex:
            print(f"Character lookup error: {ex}")
            await ctx.send("❌ Something went wrong.")

async def setup(bot):
    await bot.add_cog(BloodTrials(bot))
