# cogs/fun.py — meme, roast, 8ball, poll, remind, snipe, deadchat,
#               interactions, afk, custom commands, say/announce/pingrole

import random
import asyncio
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
import aiohttp

from cogs.utils import (
    DB, get_setting, set_setting, snipe_cache,
    check_cooldown, set_cooldown_ts, parse_duration, is_url
)

EIGHTBALL = [
    "✅ It is certain.",      "✅ Without a doubt.",
    "✅ Yes, definitely.",    "✅ As I see it, yes.",
    "✅ You may rely on it.", "🤔 Reply hazy, try again.",
    "🤔 Ask again later.",    "🤔 Better not tell you now.",
    "❌ Don't count on it.",  "❌ Very doubtful.",
    "❌ My sources say no.",  "❌ Outlook not so good.",
]

DEADCHAT_LINES = [
    "💀 Chat is dead... someone say something pls",
    "🪦 RIP chat. Died of boredom.",
    "👀 Hello? Anyone alive out there?",
    "🦗 *cricket noises intensify*",
    "📻 ...static...",
    "🕯️ We gather here today to mourn the loss of conversation...",
    "😴 Chat fell asleep. Shake it awake!",
    "🌵 Dry in here. Someone water this chat.",
]

GIF_URLS = {
    "hug":   "https://media.tenor.com/BfWfOXsnfOkAAAAC/hug-anime.gif",
    "slap":  "https://media.tenor.com/u7R5gKgkbEkAAAAC/anime-slap.gif",
    "bite":  "https://media.tenor.com/JXymFdGh-ZIAAAAC/anime-bite.gif",
    "punch": "https://media.tenor.com/CUxJgbZ_3IQAAAAC/anime-punch.gif",
    "kick":  "https://media.tenor.com/ZXxsmWdJMNwAAAAC/kick-anime.gif",
}
GIF_META = {
    "hug":   ("🤗", 0xFF69B4),
    "slap":  ("👋", 0xFF4500),
    "bite":  ("😬", 0x8B0000),
    "punch": ("👊", 0xFF6600),
    "kick":  ("🦵", 0xFFAA00),
}

import google.generativeai as genai
import os
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
gemini_model  = genai.GenerativeModel('gemini-2.0-flash')
chat_sessions: dict[int, object] = {}

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder_task.start()

    def cog_unload(self):
        self.reminder_task.cancel()

    # ── Reminder background task ──────────────────────────────
    @tasks.loop(minutes=1)
    async def reminder_task(self):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT id,user_id,channel_id,message FROM reminders WHERE remind_at <= ?",
                (now,)
            ) as cur:
                due = await cur.fetchall()
            if due:
                await db.execute("DELETE FROM reminders WHERE remind_at <= ?", (now,))
                await db.commit()
        for _, uid, cid, msg in due:
            ch = self.bot.get_channel(cid)
            if ch:
                e = discord.Embed(title="⏰ Reminder!", description=msg,
                                  color=0x57F287, timestamp=datetime.now(timezone.utc))
                await ch.send(f"<@{uid}>", embed=e)

    # ── Gemini (on_message handled in logging cog via bot event) ─
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # AFK — clear when they send a message
        if message.guild:
            async with aiosqlite.connect(DB) as db:
                async with db.execute(
                    "SELECT reason FROM afk WHERE user_id=? AND guild_id=?",
                    (message.author.id, message.guild.id)
                ) as cur:
                    row = await cur.fetchone()
                if row:
                    await db.execute(
                        "DELETE FROM afk WHERE user_id=? AND guild_id=?",
                        (message.author.id, message.guild.id))
                    await db.commit()
                    await message.channel.send(
                        f"👋 Welcome back {message.author.mention}! Removed your AFK.",
                        delete_after=8)

            # AFK — notify if someone pings an AFK user
            for mentioned in message.mentions:
                async with aiosqlite.connect(DB) as db:
                    async with db.execute(
                        "SELECT reason, timestamp FROM afk WHERE user_id=? AND guild_id=?",
                        (mentioned.id, message.guild.id)
                    ) as cur:
                        row = await cur.fetchone()
                if row:
                    reason, ts = row
                    await message.channel.send(
                        f"💤 **{mentioned.display_name}** is AFK: {reason or 'No reason'} "
                        f"(since <t:{int(datetime.fromisoformat(ts).timestamp())}:R>)",
                        delete_after=15)

        # Gemini chatbot
        if self.bot.user in message.mentions or (
            hasattr(message.channel, 'name') and
            message.channel.name.lower() == "ai-chat"
        ):
            async with message.channel.typing():
                uid = message.author.id
                if uid not in chat_sessions:
                    chat_sessions[uid] = gemini_model.start_chat(history=[])
                try:
                    resp = await asyncio.to_thread(
                        chat_sessions[uid].send_message, message.content
                    )
                    e = discord.Embed(description=resp.text[:4096], color=0x5865F2)
                    e.set_footer(text=f"Asked by {message.author.display_name}")
                    await message.reply(embed=e)
                except Exception as ex:
                    print("Gemini error:", ex)
                    await message.reply("Gemini is taking a break 😴")

    # ─────────────────────────────────────────────────────────
    #  SLASH COMMANDS
    # ─────────────────────────────────────────────────────────

    @app_commands.command(name="meme", description="Get a random meme")
    async def slash_meme(self, interaction: discord.Interaction):
        rem = await check_cooldown(interaction.guild_id, interaction.user.id, "meme")
        if rem > 0:
            await interaction.response.send_message(
                f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        await set_cooldown_ts(interaction.guild_id, interaction.user.id, "meme")
        await interaction.response.defer()
        async with aiohttp.ClientSession() as s:
            async with s.get("https://meme-api.com/gimme") as r:
                if r.status == 200:
                    data = await r.json()
                    e = discord.Embed(title=data.get("title","meme"), color=0xFF4500)
                    e.set_image(url=data["url"])
                    await interaction.followup.send(embed=e)
                else:
                    await interaction.followup.send("Couldn't get a meme 😭")

    @app_commands.command(name="roast", description="Roast someone with Gemini")
    async def slash_roast(self, interaction: discord.Interaction, target: str):
        rem = await check_cooldown(interaction.guild_id, interaction.user.id, "roast")
        if rem > 0:
            await interaction.response.send_message(
                f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        await set_cooldown_ts(interaction.guild_id, interaction.user.id, "roast")
        await interaction.response.defer()
        try:
            resp = await asyncio.to_thread(
                gemini_model.generate_content,
                f"Roast {target} funny & short (2 sentences max).")
            await interaction.followup.send(embed=discord.Embed(
                description=f"🔥 {resp.text}", color=0xFF4500))
        except Exception as ex:
            print("Roast error:", ex)
            await interaction.followup.send("Gemini said no roast today lol")

    @app_commands.command(name="8ball", description="Ask the magic 8-ball")
    async def slash_8ball(self, interaction: discord.Interaction, question: str):
        rem = await check_cooldown(interaction.guild_id, interaction.user.id, "8ball")
        if rem > 0:
            await interaction.response.send_message(
                f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        await set_cooldown_ts(interaction.guild_id, interaction.user.id, "8ball")
        e = discord.Embed(color=0x5865F2)
        e.add_field(name="❓ Question", value=question, inline=False)
        e.add_field(name="🎱 Answer",   value=random.choice(EIGHTBALL), inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="poll", description="Create a poll")
    @app_commands.describe(question="Question", option1="Option 1", option2="Option 2",
                           option3="Option 3", option4="Option 4")
    async def slash_poll(self, interaction: discord.Interaction,
                         question: str, option1: str, option2: str,
                         option3: str = None, option4: str = None):
        rem = await check_cooldown(interaction.guild_id, interaction.user.id, "poll")
        if rem > 0:
            await interaction.response.send_message(
                f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        await set_cooldown_ts(interaction.guild_id, interaction.user.id, "poll")
        opts   = [o for o in [option1, option2, option3, option4] if o]
        emojis = ["1️⃣","2️⃣","3️⃣","4️⃣"]
        e = discord.Embed(title=f"📊 {question}", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        for i, opt in enumerate(opts):
            e.add_field(name=f"{emojis[i]} Option {i+1}", value=opt, inline=False)
        e.set_footer(text=f"Poll by {interaction.user.display_name}")
        await interaction.response.send_message(embed=e)
        msg = await interaction.original_response()
        for i in range(len(opts)):
            await msg.add_reaction(emojis[i])

    @app_commands.command(name="remind", description="Set a reminder (e.g. 30m, 2h, 1d)")
    async def slash_remind(self, interaction: discord.Interaction,
                           time: str, message: str):
        delta = parse_duration(time)
        if not delta:
            await interaction.response.send_message(
                "❌ Use e.g. `30m`, `2h`, `1d`.", ephemeral=True); return
        remind_at = datetime.now(timezone.utc) + delta
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO reminders (user_id,channel_id,message,remind_at) VALUES (?,?,?,?)",
                (interaction.user.id, interaction.channel.id, message, remind_at.isoformat()))
            await db.commit()
        e = discord.Embed(
            description=f"⏰ Reminder set for **{time}** from now!\n> {message}",
            color=0x57F287, timestamp=remind_at)
        e.set_footer(text="I'll ping you here")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="snipe", description="Show last deleted messages")
    async def slash_snipe(self, interaction: discord.Interaction):
        cache = snipe_cache.get(interaction.channel.id, [])
        if not cache:
            await interaction.response.send_message("Nothing to snipe! 🏹", ephemeral=True)
            return
        embeds = []
        for i, msg in enumerate(cache, 1):
            e = discord.Embed(description=msg["content"], color=0x5865F2, timestamp=msg["time"])
            e.set_author(name=msg["author"], icon_url=msg["avatar"])
            e.set_footer(text=f"Deleted message #{i}")
            embeds.append(e)
        await interaction.response.send_message(embeds=embeds)

    @app_commands.command(name="afk", description="Set yourself as AFK")
    async def slash_afk(self, interaction: discord.Interaction, reason: str = None):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO afk (user_id,guild_id,reason,timestamp) VALUES (?,?,?,?)",
                (interaction.user.id, interaction.guild.id, reason,
                 datetime.now(timezone.utc).isoformat()))
            await db.commit()
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"💤 You're now AFK: {reason or 'No reason'}",
                color=0x5865F2))

    @app_commands.command(name="deadchat", description="Revive a dead chat")
    async def slash_deadchat(self, interaction: discord.Interaction):
        perm_role_id = await get_setting(interaction.guild.id, 'deadchat_perm_role')
        if perm_role_id:
            has_perm = (any(r.id == perm_role_id for r in interaction.user.roles) or
                        interaction.user.guild_permissions.administrator)
            if not has_perm:
                role = interaction.guild.get_role(perm_role_id)
                await interaction.response.send_message(
                    f"❌ Only **{role.name if role else 'a specific role'}** can use this.",
                    ephemeral=True); return
        rem = await check_cooldown(interaction.guild_id, interaction.user.id, "deadchat")
        if rem > 0:
            m, s = divmod(int(rem), 60)
            await interaction.response.send_message(
                f"⏳ Deadchat on cooldown! **{'%dm %ds' % (m,s) if m else '%ds' % s}**",
                ephemeral=True); return
        await set_cooldown_ts(interaction.guild_id, interaction.user.id, "deadchat")
        ping_id = await get_setting(interaction.guild.id, 'deadchat_role_id')
        content = (f"<@&{ping_id}> " if ping_id else "") + random.choice(DEADCHAT_LINES)
        await interaction.response.send_message(
            embed=discord.Embed(description=content, color=0x5865F2),
            allowed_mentions=discord.AllowedMentions.all())

    # ── Announcement commands ─────────────────────────────────
    @app_commands.command(name="say", description="Make the bot say something")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_say(self, interaction: discord.Interaction,
                        message: str, role: discord.Role = None):
        content = (f"{role.mention} " if role else "") + message
        await interaction.response.send_message("✅ Sent!", ephemeral=True)
        await interaction.channel.send(content, allowed_mentions=discord.AllowedMentions.all())

    @app_commands.command(name="announce", description="Send to any channel")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_announce(self, interaction: discord.Interaction,
                             channel: discord.TextChannel,
                             message: str, role: discord.Role = None):
        content = (f"{role.mention} " if role else "") + message
        await interaction.response.send_message(f"✅ Sent to {channel.mention}", ephemeral=True)
        await channel.send(content, allowed_mentions=discord.AllowedMentions.all())

    @app_commands.command(name="pingrole", description="Ping a role")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_pingrole(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.send_message(
            f"{role.mention}", allowed_mentions=discord.AllowedMentions.all())

    # ── Interaction GIF commands ──────────────────────────────
    def _gif_embed(self, action: str, actor: discord.Member, target: discord.Member):
        emoji, color = GIF_META[action]
        e = discord.Embed(
            description=f"{actor.mention} {emoji} **{action}s** {target.mention}!",
            color=color)
        e.set_image(url=GIF_URLS[action])
        return e

    @app_commands.command(name="hug",      description="Hug someone 🤗")
    async def hug(self, i: discord.Interaction, member: discord.Member):
        await i.response.send_message(embed=self._gif_embed("hug", i.user, member))

    @app_commands.command(name="slap",     description="Slap someone 👋")
    async def slap(self, i: discord.Interaction, member: discord.Member):
        await i.response.send_message(embed=self._gif_embed("slap", i.user, member))

    @app_commands.command(name="bite",     description="Bite someone 😬")
    async def bite(self, i: discord.Interaction, member: discord.Member):
        await i.response.send_message(embed=self._gif_embed("bite", i.user, member))

    @app_commands.command(name="punch",    description="Punch someone 👊")
    async def punch(self, i: discord.Interaction, member: discord.Member):
        await i.response.send_message(embed=self._gif_embed("punch", i.user, member))

    @app_commands.command(name="kick_fun", description="Kick someone (playfully) 🦵")
    async def kick_fun(self, i: discord.Interaction, member: discord.Member):
        await i.response.send_message(embed=self._gif_embed("kick", i.user, member))

    # ─────────────────────────────────────────────────────────
    #  PREFIX COMMANDS
    # ─────────────────────────────────────────────────────────

    @commands.command(name="meme")
    async def prefix_meme(self, ctx: commands.Context):
        rem = await check_cooldown(ctx.guild.id, ctx.author.id, "meme")
        if rem > 0:
            await ctx.send(f"⏳ Wait **{rem:.1f}s**."); return
        await set_cooldown_ts(ctx.guild.id, ctx.author.id, "meme")
        async with aiohttp.ClientSession() as s:
            async with s.get("https://meme-api.com/gimme") as r:
                if r.status == 200:
                    data = await r.json()
                    e = discord.Embed(title=data.get("title","meme"), color=0xFF4500)
                    e.set_image(url=data["url"])
                    await ctx.send(embed=e)

    @commands.command(name="roast")
    async def prefix_roast(self, ctx: commands.Context, *, target: str):
        rem = await check_cooldown(ctx.guild.id, ctx.author.id, "roast")
        if rem > 0:
            await ctx.send(f"⏳ Wait **{rem:.1f}s**."); return
        await set_cooldown_ts(ctx.guild.id, ctx.author.id, "roast")
        try:
            resp = await asyncio.to_thread(
                gemini_model.generate_content,
                f"Roast {target} funny & short (2 sentences max).")
            await ctx.send(embed=discord.Embed(
                description=f"🔥 {resp.text}", color=0xFF4500))
        except Exception:
            await ctx.send("Gemini said no roast today lol")

    @commands.command(name="8ball")
    async def prefix_8ball(self, ctx: commands.Context, *, question: str):
        e = discord.Embed(color=0x5865F2)
        e.add_field(name="❓", value=question, inline=False)
        e.add_field(name="🎱", value=random.choice(EIGHTBALL), inline=False)
        await ctx.send(embed=e)

    @commands.command(name="poll")
    async def prefix_poll(self, ctx: commands.Context, *, args: str):
        """Usage: ?poll Question | Option1 | Option2 | Option3"""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 3:
            await ctx.send("❌ Format: `?poll Question | Option1 | Option2`"); return
        question, *opts = parts
        opts   = opts[:4]
        emojis = ["1️⃣","2️⃣","3️⃣","4️⃣"]
        e = discord.Embed(title=f"📊 {question}", color=0x5865F2)
        for i, opt in enumerate(opts):
            e.add_field(name=f"{emojis[i]} Option {i+1}", value=opt, inline=False)
        e.set_footer(text=f"Poll by {ctx.author.display_name}")
        msg = await ctx.send(embed=e)
        for i in range(len(opts)):
            await msg.add_reaction(emojis[i])

    @commands.command(name="remind")
    async def prefix_remind(self, ctx: commands.Context, time: str, *, message: str):
        """Usage: ?remind 30m Pick up groceries"""
        delta = parse_duration(time)
        if not delta:
            await ctx.send("❌ Use e.g. `30m`, `2h`, `1d`."); return
        remind_at = datetime.now(timezone.utc) + delta
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO reminders (user_id,channel_id,message,remind_at) VALUES (?,?,?,?)",
                (ctx.author.id, ctx.channel.id, message, remind_at.isoformat()))
            await db.commit()
        await ctx.send(embed=discord.Embed(
            description=f"⏰ Reminder set for **{time}**!\n> {message}",
            color=0x57F287))

    @commands.command(name="snipe")
    async def prefix_snipe(self, ctx: commands.Context):
        cache = snipe_cache.get(ctx.channel.id, [])
        if not cache:
            await ctx.send("Nothing to snipe! 🏹"); return
        embeds = []
        for i, msg in enumerate(cache, 1):
            e = discord.Embed(description=msg["content"], color=0x5865F2, timestamp=msg["time"])
            e.set_author(name=msg["author"], icon_url=msg["avatar"])
            e.set_footer(text=f"Deleted message #{i}")
            embeds.append(e)
        await ctx.send(embeds=embeds)

    @commands.command(name="afk")
    async def prefix_afk(self, ctx: commands.Context, *, reason: str = None):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO afk (user_id,guild_id,reason,timestamp) VALUES (?,?,?,?)",
                (ctx.author.id, ctx.guild.id, reason,
                 datetime.now(timezone.utc).isoformat()))
            await db.commit()
        await ctx.send(embed=discord.Embed(
            description=f"💤 You're now AFK: {reason or 'No reason'}",
            color=0x5865F2))

    @commands.command(name="deadchat")
    async def prefix_deadchat(self, ctx: commands.Context):
        perm_role_id = await get_setting(ctx.guild.id, 'deadchat_perm_role')
        if perm_role_id:
            has_perm = (any(r.id == perm_role_id for r in ctx.author.roles) or
                        ctx.author.guild_permissions.administrator)
            if not has_perm:
                await ctx.send("❌ You don't have permission for deadchat."); return
        rem = await check_cooldown(ctx.guild.id, ctx.author.id, "deadchat")
        if rem > 0:
            m, s = divmod(int(rem), 60)
            await ctx.send(f"⏳ Cooldown: **{'%dm %ds' % (m,s) if m else '%ds' % s}**"); return
        await set_cooldown_ts(ctx.guild.id, ctx.author.id, "deadchat")
        ping_id = await get_setting(ctx.guild.id, 'deadchat_role_id')
        content = (f"<@&{ping_id}> " if ping_id else "") + random.choice(DEADCHAT_LINES)
        await ctx.send(embed=discord.Embed(description=content, color=0x5865F2),
                       allowed_mentions=discord.AllowedMentions.all())

    @commands.command(name="say")
    @commands.has_permissions(manage_messages=True)
    async def prefix_say(self, ctx: commands.Context, *, message: str):
        await ctx.message.delete()
        await ctx.send(message)

    @commands.command(name="announce")
    @commands.has_permissions(manage_messages=True)
    async def prefix_announce(self, ctx: commands.Context,
                              channel: discord.TextChannel, *, message: str):
        await channel.send(message)
        await ctx.send(f"✅ Sent to {channel.mention}", delete_after=5)

    @commands.command(name="pingrole")
    @commands.has_permissions(manage_messages=True)
    async def prefix_pingrole(self, ctx: commands.Context, role: discord.Role):
        await ctx.send(f"{role.mention}", allowed_mentions=discord.AllowedMentions.all())

    @commands.command(name="hug")
    async def prefix_hug(self, ctx: commands.Context, member: discord.Member):
        await ctx.send(embed=self._gif_embed("hug", ctx.author, member))

    @commands.command(name="slap")
    async def prefix_slap(self, ctx: commands.Context, member: discord.Member):
        await ctx.send(embed=self._gif_embed("slap", ctx.author, member))

    @commands.command(name="bite")
    async def prefix_bite(self, ctx: commands.Context, member: discord.Member):
        await ctx.send(embed=self._gif_embed("bite", ctx.author, member))

    @commands.command(name="punch")
    async def prefix_punch(self, ctx: commands.Context, member: discord.Member):
        await ctx.send(embed=self._gif_embed("punch", ctx.author, member))

    # ─────────────────────────────────────────────────────────
    #  CUSTOM COMMANDS
    # ─────────────────────────────────────────────────────────

    @app_commands.command(name="addcommand", description="Add a custom command")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        name="Command name (without prefix)",
        action_type="What it does",
        value="The message, @mention target, or command name to alias"
    )
    @app_commands.choices(action_type=[
        app_commands.Choice(name="message — send a text response",      value="message"),
        app_commands.Choice(name="ping    — ping a user or role",       value="ping"),
        app_commands.Choice(name="alias   — run another command",       value="alias"),
    ])
    async def addcommand(self, interaction: discord.Interaction,
                         name: str, action_type: str, value: str):
        name = name.lower().strip()
        async with aiosqlite.connect(DB) as db:
            try:
                await db.execute(
                    "INSERT INTO custom_commands (guild_id,name,action_type,value) VALUES (?,?,?,?)",
                    (interaction.guild.id, name, action_type, value))
                await db.commit()
            except aiosqlite.IntegrityError:
                await interaction.response.send_message(
                    f"❌ A command named `{name}` already exists. Delete it first.",
                    ephemeral=True); return
        e = discord.Embed(title="✅ Custom Command Added", color=0x57F287)
        e.add_field(name="Name",   value=f"`{name}`")
        e.add_field(name="Type",   value=action_type)
        e.add_field(name="Value",  value=value, inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="listcommands", description="List all custom commands")
    async def listcommands(self, interaction: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT name,action_type,value FROM custom_commands WHERE guild_id=?",
                (interaction.guild.id,)
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            await interaction.response.send_message("No custom commands set.", ephemeral=True)
            return
        e = discord.Embed(title=f"⚡ Custom Commands ({len(rows)})", color=0x5865F2)
        for name, atype, value in rows:
            e.add_field(name=f"`{name}` [{atype}]", value=value[:80], inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="deletecommand", description="Delete a custom command")
    @app_commands.default_permissions(manage_guild=True)
    async def deletecommand(self, interaction: discord.Interaction, name: str):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "DELETE FROM custom_commands WHERE guild_id=? AND name=?",
                (interaction.guild.id, name.lower()))
            await db.commit()
        if cur.rowcount:
            await interaction.response.send_message(
                f"🗑️ Deleted `{name}`.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ No command named `{name}`.", ephemeral=True)

    @commands.Cog.listener()
    async def on_message_custom_commands(self, message: discord.Message):
        """Handled via on_message in logging cog — custom commands run via prefix listener."""
        pass

async def setup(bot):
    await bot.add_cog(Fun(bot))
