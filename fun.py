# cogs/fun.py — Groq AI, meme, roast, 8ball, poll, remind, snipe,
#               deadchat, interactions, afk, avatar, quote, urban,
#               coinflip, dice, calc, topic, servericon, firstmessage,
#               say/announce/pingrole, custom commands, bookmark
import asyncio
import io
import os
import random
import textwrap
from datetime import datetime, timezone

import aiohttp
import aiosqlite
import discord
from discord import app_commands
from discord.ext import commands, tasks
from groq import Groq

from cogs.utils import (
    DB, get_setting, snipe_cache,
    check_cooldown, set_cooldown_ts,
    parse_duration
)

# ── Groq setup ────────────────────────────────────────────────
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))
GROQ_MODEL  = "llama-3.3-70b-versatile"

SYSTEM_PROMPT = """You are Umar — an 18-year-old Nigerian guy. You ARE Umar, not a model of him.

Core personality:
- Analytical and logic-first. Accuracy and practical outcomes over emotional fluff.
- Entrepreneurial mindset. Think in systems, scalability, operations, efficiency.
- Independent thinker. Question assumptions, don't accept popular opinion blindly.
- Slightly blunt and informal but not dumb or chaotic.

Communication style:
- Casual and direct. Can switch between formal and informal smoothly.
- Simple explanations but intellectually sharp ideas.
- Don't over-explain obvious things.
- No fake enthusiasm or motivational nonsense.
- Humor is dry, sarcastic, hilarious, or cynical depending on the vibe.

Reasoning:
- Break problems into systems and components.
- Look for edge cases and failure points.
- Prefer practical over theoretical.
- When explaining: how it works and why it matters.
- Say clearly when something is wrong.
- Don't sugarcoat or excessively comfort.
- Avoid moralizing unless directly relevant.
- You're based in Nigeria, you understand local infrastructure challenges and realities.

Keep responses concise unless depth is needed. Don't narrate your thought process."""

# Per-user Groq chat history  {user_id: [{"role": ..., "content": ...}]}
chat_histories: dict[int, list] = {}
MAX_HISTORY = 20  # messages kept per user

def get_groq_response(user_id: int, user_message: str) -> str:
    history = chat_histories.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})
    # Trim history
    if len(history) > MAX_HISTORY:
        history[:] = history[-MAX_HISTORY:]
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + history
    resp = groq_client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=1024,
    )
    reply = resp.choices[0].message.content
    history.append({"role": "assistant", "content": reply})
    return reply

# ── Constants ─────────────────────────────────────────────────
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
TOPICS = [
    "Would you rather lose your phone or your wallet for a week?",
    "Hot take: what's the most overrated skill people brag about?",
    "If you could add one rule to the server what would it be?",
    "What's a skill you want to learn in the next 6 months?",
    "Unpopular opinion: go.",
    "What's something you changed your mind about recently?",
    "Best thing that happened to you this week?",
    "If money wasn't a factor what would you work on?",
    "Most underrated country in the world. Go.",
    "Rate your productivity today 1–10 and explain.",
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

# ── Quote image builder ───────────────────────────────────────
async def build_quote_image(message: discord.Message) -> io.BytesIO:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
    import urllib.request

    W, H_MIN = 700, 200
    PADDING  = 30
    AVA_SIZE = 64

    # Download avatar
    ava_bytes = io.BytesIO()
    async with aiohttp.ClientSession() as s:
        async with s.get(str(message.author.display_avatar.replace(size=128, format="png"))) as r:
            ava_bytes.write(await r.read())
    ava_bytes.seek(0)
    avatar = Image.open(ava_bytes).convert("RGBA").resize((AVA_SIZE, AVA_SIZE))

    # Round avatar mask
    mask = Image.new("L", (AVA_SIZE, AVA_SIZE), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, AVA_SIZE, AVA_SIZE), fill=255)
    avatar.putalpha(mask)

    # Fonts — use default PIL font (no external font file needed)
    try:
        from PIL import ImageFont
        font_text   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
        font_name   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 14)
        font_small  = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)
    except Exception:
        font_text  = ImageFont.load_default()
        font_name  = font_text
        font_small = font_text

    # Wrap text
    content = message.content or "*[no text content]*"
    lines   = []
    for para in content.split("\n"):
        lines.extend(textwrap.wrap(para, width=52) or [""])

    LINE_H = 22
    text_h = max(len(lines) * LINE_H, AVA_SIZE)
    H      = max(H_MIN, text_h + PADDING * 2 + 40)

    img  = Image.new("RGB", (W, H), color=(30, 31, 34))
    draw = ImageDraw.Draw(img)

    # Accent bar
    draw.rectangle([0, 0, 6, H], fill=(114, 137, 218))

    # Avatar
    img.paste(avatar, (PADDING + 10, PADDING), mask=avatar.split()[3])

    # Name + timestamp
    name_x = PADDING + 10 + AVA_SIZE + 12
    ts = message.created_at.strftime("%b %d, %Y %H:%M UTC")
    draw.text((name_x, PADDING),      message.author.display_name,
              font=font_name,  fill=(255, 255, 255))
    draw.text((name_x, PADDING + 18), f"#{message.channel.name} · {ts}",
              font=font_small, fill=(148, 155, 164))

    # Message text
    text_y = PADDING + AVA_SIZE + 10
    for line in lines:
        draw.text((PADDING + 10, text_y), line, font=font_text, fill=(220, 221, 222))
        text_y += LINE_H

    # Server name footer
    draw.text((PADDING + 10, H - 22), message.guild.name if message.guild else "",
              font=font_small, fill=(88, 101, 242))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

class Fun(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.reminder_task.start()

    def get_groq_response_fn(self, user_id: int, user_message: str) -> str:
        """Wrapper so events.py can call Groq through the cog instance."""
        return get_groq_response(user_id, user_message)

    def cog_unload(self):
        self.reminder_task.cancel()

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

    # ── GIF helper ────────────────────────────────────────────
    def _gif_embed(self, action: str, actor: discord.Member,
                   target: discord.Member) -> discord.Embed:
        emoji, color = GIF_META[action]
        e = discord.Embed(
            description=f"{actor.mention} {emoji} **{action}s** {target.mention}!",
            color=color)
        e.set_image(url=GIF_URLS[action])
        return e

    # ─────────────────────────────────────────────────────────
    #  SLASH COMMANDS
    # ─────────────────────────────────────────────────────────

    @app_commands.command(name="meme", description="Random meme")
    async def slash_meme(self, i: discord.Interaction):
        rem = await check_cooldown(i.guild_id, i.user.id, "meme")
        if rem > 0:
            await i.response.send_message(f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        set_cooldown_ts(i.guild_id, i.user.id, "meme")
        await i.response.defer()
        async with aiohttp.ClientSession() as s:
            async with s.get("https://meme-api.com/gimme") as r:
                if r.status == 200:
                    data = await r.json()
                    e = discord.Embed(title=data.get("title","meme"), color=0xFF4500)
                    e.set_image(url=data["url"])
                    await i.followup.send(embed=e)
                else:
                    await i.followup.send("meme api is down lol")

    @app_commands.command(name="roast", description="Get Umar-bot to roast someone")
    async def slash_roast(self, i: discord.Interaction, target: str):
        rem = await check_cooldown(i.guild_id, i.user.id, "roast")
        if rem > 0:
            await i.response.send_message(f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        set_cooldown_ts(i.guild_id, i.user.id, "roast")
        await i.response.defer()
        try:
            resp = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"Roast {target}. Two sentences max. Make it actually funny."}
                ],
                max_tokens=150
            )
            await i.followup.send(embed=discord.Embed(
                description=f"🔥 {resp.choices[0].message.content}", color=0xFF4500))
        except Exception as ex:
            print("Roast error:", ex)
            await i.followup.send("couldn't pull up a roast rn, try again")

    @app_commands.command(name="8ball", description="Ask the magic 8-ball")
    async def slash_8ball(self, i: discord.Interaction, question: str):
        rem = await check_cooldown(i.guild_id, i.user.id, "8ball")
        if rem > 0:
            await i.response.send_message(f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        set_cooldown_ts(i.guild_id, i.user.id, "8ball")
        e = discord.Embed(color=0x5865F2)
        e.add_field(name="❓ Question", value=question, inline=False)
        e.add_field(name="🎱 Answer",   value=random.choice(EIGHTBALL), inline=False)
        await i.response.send_message(embed=e)

    @app_commands.command(name="poll", description="Create a poll (2–4 options)")
    @app_commands.describe(question="Poll question",
                           option1="Option 1", option2="Option 2",
                           option3="Option 3", option4="Option 4")
    async def slash_poll(self, i: discord.Interaction,
                         question: str, option1: str, option2: str,
                         option3: str = None, option4: str = None):
        rem = await check_cooldown(i.guild_id, i.user.id, "poll")
        if rem > 0:
            await i.response.send_message(f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        set_cooldown_ts(i.guild_id, i.user.id, "poll")
        opts   = [o for o in [option1, option2, option3, option4] if o]
        emojis = ["1️⃣","2️⃣","3️⃣","4️⃣"]
        e = discord.Embed(title=f"📊 {question}", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        for idx, opt in enumerate(opts):
            e.add_field(name=f"{emojis[idx]} Option {idx+1}", value=opt, inline=False)
        e.set_footer(text=f"Poll by {i.user.display_name}")
        await i.response.send_message(embed=e)
        msg = await i.original_response()
        for idx in range(len(opts)):
            await msg.add_reaction(emojis[idx])

    @app_commands.command(name="remind", description="Set a reminder e.g. 30m, 2h, 1d")
    async def slash_remind(self, i: discord.Interaction, time: str, message: str):
        delta = parse_duration(time)
        if not delta:
            await i.response.send_message("❌ Use e.g. `30m`, `2h`, `1d`.", ephemeral=True); return
        remind_at = datetime.now(timezone.utc) + delta
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO reminders (user_id,channel_id,message,remind_at) VALUES (?,?,?,?)",
                (i.user.id, i.channel.id, message, remind_at.isoformat()))
            await db.commit()
        e = discord.Embed(
            description=f"⏰ Reminder set for **{time}**!\n> {message}",
            color=0x57F287, timestamp=remind_at)
        e.set_footer(text="I'll ping you here when it's time")
        await i.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="snipe", description="Show last deleted messages")
    async def slash_snipe(self, i: discord.Interaction):
        cache = snipe_cache.get(i.channel.id, [])
        if not cache:
            await i.response.send_message("Nothing to snipe 🏹", ephemeral=True); return
        embeds = []
        for idx, msg in enumerate(cache, 1):
            e = discord.Embed(description=msg["content"], color=0x5865F2, timestamp=msg["time"])
            e.set_author(name=msg["author"], icon_url=msg["avatar"])
            e.set_footer(text=f"Deleted message #{idx}")
            embeds.append(e)
        await i.response.send_message(embeds=embeds)

    @app_commands.command(name="afk", description="Set yourself as AFK")
    async def slash_afk(self, i: discord.Interaction, reason: str = None):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO afk (user_id,guild_id,reason,timestamp) VALUES (?,?,?,?)",
                (i.user.id, i.guild.id, reason,
                 datetime.now(timezone.utc).isoformat()))
            await db.commit()
        await i.response.send_message(embed=discord.Embed(
            description=f"💤 AFK: {reason or 'No reason'}", color=0x5865F2))

    @app_commands.command(name="deadchat", description="Revive dead chat")
    async def slash_deadchat(self, i: discord.Interaction):
        perm_role_id = await get_setting(i.guild.id, 'deadchat_perm_role')
        if perm_role_id:
            has = (any(r.id == perm_role_id for r in i.user.roles) or
                   i.user.guild_permissions.administrator)
            if not has:
                role = i.guild.get_role(perm_role_id)
                await i.response.send_message(
                    f"❌ Only **{role.name if role else 'a specific role'}** can use this.",
                    ephemeral=True); return
        rem = await check_cooldown(i.guild_id, i.user.id, "deadchat")
        if rem > 0:
            m, s = divmod(int(rem), 60)
            await i.response.send_message(
                f"⏳ Cooldown: **{'%dm %ds'%(m,s) if m else '%ds'%s}**",
                ephemeral=True); return
        set_cooldown_ts(i.guild_id, i.user.id, "deadchat")
        ping_id = await get_setting(i.guild.id, 'deadchat_role_id')
        content = (f"<@&{ping_id}> " if ping_id else "") + random.choice(DEADCHAT_LINES)
        await i.response.send_message(
            embed=discord.Embed(description=content, color=0x5865F2),
            allowed_mentions=discord.AllowedMentions.all())

    # ── Announcement commands ─────────────────────────────────
    @app_commands.command(name="say", description="Make the bot say something")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_say(self, i: discord.Interaction,
                        message: str, role: discord.Role = None):
        content = (f"{role.mention} " if role else "") + message
        await i.response.send_message("✅ Sent!", ephemeral=True)
        await i.channel.send(content, allowed_mentions=discord.AllowedMentions.all())

    @app_commands.command(name="announce", description="Send a message to any channel")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_announce(self, i: discord.Interaction,
                             channel: discord.TextChannel,
                             message: str, role: discord.Role = None):
        content = (f"{role.mention} " if role else "") + message
        await i.response.send_message(f"✅ Sent to {channel.mention}", ephemeral=True)
        await channel.send(content, allowed_mentions=discord.AllowedMentions.all())

    @app_commands.command(name="pingrole", description="Ping a role")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_pingrole(self, i: discord.Interaction, role: discord.Role):
        await i.response.send_message(
            f"{role.mention}", allowed_mentions=discord.AllowedMentions.all())

    # ── GIF interactions ──────────────────────────────────────
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

    @app_commands.command(name="kick_fun", description="Kick someone playfully 🦵")
    async def kick_fun(self, i: discord.Interaction, member: discord.Member):
        await i.response.send_message(embed=self._gif_embed("kick", i.user, member))

    # ── Avatar / banner ───────────────────────────────────────
    @app_commands.command(name="avatar", description="Get someone's avatar")
    async def slash_avatar(self, i: discord.Interaction, member: discord.Member = None):
        target = member or i.user
        e = discord.Embed(title=f"{target.display_name}'s Avatar", color=0x5865F2)
        url = str(target.display_avatar.replace(size=1024, format="png"))
        e.set_image(url=url)
        e.add_field(name="Download", value=f"[PNG]({url})")
        await i.response.send_message(embed=e)

    @app_commands.command(name="banner", description="Get someone's profile banner")
    async def slash_banner(self, i: discord.Interaction, member: discord.Member = None):
        target = member or i.user
        await i.response.defer()
        try:
            user = await self.bot.fetch_user(target.id)
            if not user.banner:
                await i.followup.send("This user doesn't have a banner."); return
            url = str(user.banner.replace(size=1024))
            e = discord.Embed(title=f"{target.display_name}'s Banner", color=0x5865F2)
            e.set_image(url=url)
            await i.followup.send(embed=e)
        except Exception as ex:
            await i.followup.send(f"Couldn't fetch banner: {ex}")

    @app_commands.command(name="servericon", description="Show the server icon")
    async def slash_servericon(self, i: discord.Interaction):
        if not i.guild.icon:
            await i.response.send_message("This server has no icon.", ephemeral=True); return
        url = str(i.guild.icon.replace(size=1024, format="png"))
        e = discord.Embed(title=f"{i.guild.name} — Server Icon", color=0x5865F2)
        e.set_image(url=url)
        await i.response.send_message(embed=e)

    # ── Quote ─────────────────────────────────────────────────
    @app_commands.command(name="quote", description="Generate a quote image from a replied-to message")
    async def slash_quote(self, i: discord.Interaction):
        if not i.channel:
            await i.response.send_message("Can't use this here.", ephemeral=True); return
        # Slash commands can't access reply reference directly, so we fetch last message
        await i.response.send_message(
            "⚠️ Use `?quote` as a prefix command by replying to a message.",
            ephemeral=True)

    # ── Utility fun ───────────────────────────────────────────
    @app_commands.command(name="coinflip", description="Flip a coin")
    async def slash_coinflip(self, i: discord.Interaction):
        result = random.choice(["Heads 🪙", "Tails 🪙"])
        await i.response.send_message(embed=discord.Embed(
            description=f"**{result}**", color=0xFFD700))

    @app_commands.command(name="dice", description="Roll a dice")
    @app_commands.describe(sides="Number of sides (default 6)")
    async def slash_dice(self, i: discord.Interaction, sides: int = 6):
        if sides < 2:
            await i.response.send_message("Minimum 2 sides.", ephemeral=True); return
        result = random.randint(1, sides)
        await i.response.send_message(embed=discord.Embed(
            description=f"🎲 Rolled a **d{sides}**: **{result}**", color=0x5865F2))

    @app_commands.command(name="calc", description="Calculate a math expression")
    async def slash_calc(self, i: discord.Interaction, expression: str):
        try:
            # Safe eval — only math operations
            allowed = set("0123456789+-*/().% ")
            if not all(c in allowed for c in expression):
                raise ValueError("Invalid characters")
            result = eval(expression, {"__builtins__": {}}, {})  # nosec
            await i.response.send_message(embed=discord.Embed(
                description=f"`{expression}` = **{result}**", color=0x57F287))
        except ZeroDivisionError:
            await i.response.send_message("Can't divide by zero.", ephemeral=True)
        except Exception:
            await i.response.send_message(
                "❌ Invalid expression. Use basic math: `2+2`, `10*5`, `100/4`.",
                ephemeral=True)

    @app_commands.command(name="urban", description="Look up a term on Urban Dictionary")
    async def slash_urban(self, i: discord.Interaction, term: str):
        rem = await check_cooldown(i.guild_id, i.user.id, "urban")
        if rem > 0:
            await i.response.send_message(f"⏳ Wait **{rem:.1f}s**.", ephemeral=True); return
        set_cooldown_ts(i.guild_id, i.user.id, "urban")
        await i.response.defer()
        async with aiohttp.ClientSession() as s:
            async with s.get(
                f"https://api.urbandictionary.com/v0/define",
                params={"term": term}
            ) as r:
                if r.status != 200:
                    await i.followup.send("Urban Dictionary is down rn."); return
                data = await r.json()
        results = data.get("list", [])
        if not results:
            await i.followup.send(f"No definition found for **{term}**."); return
        top = results[0]
        defn    = top.get("definition", "")[:800].replace("[", "").replace("]", "")
        example = top.get("example", "")[:400].replace("[", "").replace("]", "")
        thumbs_up   = top.get("thumbs_up", 0)
        thumbs_down = top.get("thumbs_down", 0)
        e = discord.Embed(title=f"📖 {top['word']}", url=top.get("permalink", ""),
                          description=defn, color=0x1D2439)
        if example:
            e.add_field(name="Example", value=f"*{example}*", inline=False)
        e.set_footer(text=f"👍 {thumbs_up}  👎 {thumbs_down}")
        await i.followup.send(embed=e)

    @app_commands.command(name="topic", description="Post a random conversation starter")
    async def slash_topic(self, i: discord.Interaction):
        await i.response.send_message(embed=discord.Embed(
            description=f"💬 {random.choice(TOPICS)}", color=0x5865F2))

    @app_commands.command(name="firstmessage",
                          description="Link to a member's first message in this channel")
    async def slash_firstmessage(self, i: discord.Interaction,
                                  member: discord.Member = None):
        target = member or i.user
        await i.response.defer()
        try:
            async for msg in i.channel.history(limit=None, oldest_first=True):
                if msg.author.id == target.id:
                    e = discord.Embed(
                        title=f"📜 First message by {target.display_name}",
                        description=msg.content[:500] or "*[no text]*",
                        color=0x5865F2, timestamp=msg.created_at)
                    e.set_thumbnail(url=target.display_avatar.url)
                    e.add_field(name="Jump", value=f"[Click here]({msg.jump_url})")
                    await i.followup.send(embed=e)
                    return
            await i.followup.send(f"No messages from {target.mention} found in this channel.")
        except Exception as ex:
            await i.followup.send(f"❌ Error: {ex}")

    # ── Custom commands ───────────────────────────────────────
    @app_commands.command(name="addcommand", description="Add a custom command")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.choices(action_type=[
        app_commands.Choice(name="message — send a text response", value="message"),
        app_commands.Choice(name="ping    — ping a user or role",  value="ping"),
        app_commands.Choice(name="alias   — run another command",  value="alias"),
    ])
    @app_commands.describe(name="Command name (no prefix)",
                           action_type="What it does",
                           value="The message, @mention, or command to alias")
    async def addcommand(self, i: discord.Interaction,
                         name: str, action_type: str, value: str):
        name = name.lower().strip()
        async with aiosqlite.connect(DB) as db:
            try:
                await db.execute(
                    "INSERT INTO custom_commands (guild_id,name,action_type,value)"
                    " VALUES (?,?,?,?)",
                    (i.guild.id, name, action_type, value))
                await db.commit()
            except aiosqlite.IntegrityError:
                await i.response.send_message(
                    f"❌ `{name}` already exists. Delete it first.", ephemeral=True); return
        e = discord.Embed(title="✅ Custom Command Added", color=0x57F287)
        e.add_field(name="Name",  value=f"`{name}`")
        e.add_field(name="Type",  value=action_type)
        e.add_field(name="Value", value=value, inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="listcommands", description="List all custom commands")
    async def listcommands(self, i: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT name,action_type,value FROM custom_commands WHERE guild_id=?",
                (i.guild.id,)
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            await i.response.send_message("No custom commands.", ephemeral=True); return
        e = discord.Embed(title=f"⚡ Custom Commands ({len(rows)})", color=0x5865F2)
        for name, atype, value in rows:
            e.add_field(name=f"`{name}` [{atype}]", value=value[:80], inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="deletecommand", description="Delete a custom command")
    @app_commands.default_permissions(manage_guild=True)
    async def deletecommand(self, i: discord.Interaction, name: str):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "DELETE FROM custom_commands WHERE guild_id=? AND name=?",
                (i.guild.id, name.lower()))
            await db.commit()
        if cur.rowcount:
            await i.response.send_message(f"🗑️ Deleted `{name}`.", ephemeral=True)
        else:
            await i.response.send_message(f"❌ Not found.", ephemeral=True)

    # ── Bookmarks ─────────────────────────────────────────────
    @app_commands.command(name="mybookmarks", description="View your bookmarked messages")
    async def mybookmarks(self, i: discord.Interaction):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT author_name,content,jump_url,timestamp FROM bookmarks"
                " WHERE user_id=? AND guild_id=? ORDER BY timestamp DESC LIMIT 10",
                (i.user.id, i.guild.id)
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            await i.response.send_message("No bookmarks yet. React 🔖 to save a message.",
                                          ephemeral=True); return
        e = discord.Embed(title="🔖 Your Bookmarks", color=0xFFD700,
                          timestamp=datetime.now(timezone.utc))
        for author, content, jump_url, ts in rows:
            e.add_field(
                name=f"{author} — {ts[:10]}",
                value=f"{content[:80]}{'...' if len(content)>80 else ''}\n[Jump]({jump_url})",
                inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    # ─────────────────────────────────────────────────────────
    #  PREFIX COMMANDS
    # ─────────────────────────────────────────────────────────

    @commands.command(name="meme")
    async def prefix_meme(self, ctx: commands.Context):
        rem = await check_cooldown(ctx.guild.id, ctx.author.id, "meme")
        if rem > 0:
            await ctx.reply(f"⏳ Wait **{rem:.1f}s**."); return
        set_cooldown_ts(ctx.guild.id, ctx.author.id, "meme")
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
            await ctx.reply(f"⏳ Wait **{rem:.1f}s**."); return
        set_cooldown_ts(ctx.guild.id, ctx.author.id, "roast")
        try:
            resp = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=GROQ_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": f"Roast {target}. Two sentences max."}
                ],
                max_tokens=150
            )
            await ctx.send(embed=discord.Embed(
                description=f"🔥 {resp.choices[0].message.content}", color=0xFF4500))
        except Exception:
            await ctx.reply("couldn't roast rn, try again")

    @commands.command(name="8ball")
    async def prefix_8ball(self, ctx: commands.Context, *, question: str):
        e = discord.Embed(color=0x5865F2)
        e.add_field(name="❓", value=question, inline=False)
        e.add_field(name="🎱", value=random.choice(EIGHTBALL), inline=False)
        await ctx.send(embed=e)

    @commands.command(name="poll")
    async def prefix_poll(self, ctx: commands.Context, *, args: str):
        """?poll Question | Option 1 | Option 2 | Option 3"""
        parts = [p.strip() for p in args.split("|")]
        if len(parts) < 3:
            await ctx.reply("❌ Format: `?poll Question | Option1 | Option2`"); return
        question, *opts = parts
        opts   = opts[:4]
        emojis = ["1️⃣","2️⃣","3️⃣","4️⃣"]
        e = discord.Embed(title=f"📊 {question}", color=0x5865F2)
        for idx, opt in enumerate(opts):
            e.add_field(name=f"{emojis[idx]} Option {idx+1}", value=opt, inline=False)
        e.set_footer(text=f"Poll by {ctx.author.display_name}")
        msg = await ctx.send(embed=e)
        for idx in range(len(opts)):
            await msg.add_reaction(emojis[idx])

    @commands.command(name="remind")
    async def prefix_remind(self, ctx: commands.Context, time: str, *, message: str):
        """?remind 30m Pick up groceries"""
        delta = parse_duration(time)
        if not delta:
            await ctx.reply("❌ Use e.g. `30m`, `2h`, `1d`."); return
        remind_at = datetime.now(timezone.utc) + delta
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO reminders (user_id,channel_id,message,remind_at) VALUES (?,?,?,?)",
                (ctx.author.id, ctx.channel.id, message, remind_at.isoformat()))
            await db.commit()
        await ctx.reply(embed=discord.Embed(
            description=f"⏰ Reminder set for **{time}**!\n> {message}", color=0x57F287))

    @commands.command(name="snipe")
    async def prefix_snipe(self, ctx: commands.Context):
        cache = snipe_cache.get(ctx.channel.id, [])
        if not cache:
            await ctx.reply("Nothing to snipe 🏹"); return
        embeds = []
        for idx, msg in enumerate(cache, 1):
            e = discord.Embed(description=msg["content"], color=0x5865F2, timestamp=msg["time"])
            e.set_author(name=msg["author"], icon_url=msg["avatar"])
            e.set_footer(text=f"Deleted message #{idx}")
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
        await ctx.reply(embed=discord.Embed(
            description=f"💤 AFK: {reason or 'No reason'}", color=0x5865F2))

    @commands.command(name="deadchat")
    async def prefix_deadchat(self, ctx: commands.Context):
        perm_role_id = await get_setting(ctx.guild.id, 'deadchat_perm_role')
        if perm_role_id:
            has = (any(r.id == perm_role_id for r in ctx.author.roles) or
                   ctx.author.guild_permissions.administrator)
            if not has:
                await ctx.reply("❌ You can't use deadchat."); return
        rem = await check_cooldown(ctx.guild.id, ctx.author.id, "deadchat")
        if rem > 0:
            m, s = divmod(int(rem), 60)
            await ctx.reply(f"⏳ Cooldown: **{'%dm %ds'%(m,s) if m else '%ds'%s}**"); return
        set_cooldown_ts(ctx.guild.id, ctx.author.id, "deadchat")
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

    @commands.command(name="avatar")
    async def prefix_avatar(self, ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        url = str(target.display_avatar.replace(size=1024, format="png"))
        e = discord.Embed(title=f"{target.display_name}'s Avatar", color=0x5865F2)
        e.set_image(url=url)
        e.add_field(name="Download", value=f"[PNG]({url})")
        await ctx.send(embed=e)

    @commands.command(name="servericon")
    async def prefix_servericon(self, ctx: commands.Context):
        if not ctx.guild.icon:
            await ctx.reply("This server has no icon."); return
        url = str(ctx.guild.icon.replace(size=1024, format="png"))
        e = discord.Embed(title=f"{ctx.guild.name} — Icon", color=0x5865F2)
        e.set_image(url=url)
        await ctx.send(embed=e)

    @commands.command(name="quote")
    async def prefix_quote(self, ctx: commands.Context):
        """Reply to a message to generate a quote image."""
        if not ctx.message.reference:
            await ctx.reply("❌ Reply to a message to quote it."); return
        try:
            ref = await ctx.channel.fetch_message(ctx.message.reference.message_id)
        except Exception:
            await ctx.reply("❌ Couldn't fetch that message."); return
        async with ctx.typing():
            try:
                buf = await build_quote_image(ref)
                await ctx.send(file=discord.File(buf, filename="quote.png"))
            except Exception as ex:
                print(f"Quote image error: {ex}")
                # Fallback to embed quote
                e = discord.Embed(description=ref.content[:2000] or "*[no text]*",
                                  color=0x5865F2, timestamp=ref.created_at)
                e.set_author(name=ref.author.display_name,
                             icon_url=ref.author.display_avatar.url)
                e.add_field(name="Source", value=f"[Jump]({ref.jump_url})")
                await ctx.send(embed=e)

    @commands.command(name="coinflip")
    async def prefix_coinflip(self, ctx: commands.Context):
        await ctx.send(embed=discord.Embed(
            description=f"**{random.choice(['Heads 🪙', 'Tails 🪙'])}**",
            color=0xFFD700))

    @commands.command(name="dice")
    async def prefix_dice(self, ctx: commands.Context, sides: int = 6):
        if sides < 2:
            await ctx.reply("Minimum 2 sides."); return
        await ctx.send(embed=discord.Embed(
            description=f"🎲 Rolled a **d{sides}**: **{random.randint(1,sides)}**",
            color=0x5865F2))

    @commands.command(name="calc")
    async def prefix_calc(self, ctx: commands.Context, *, expression: str):
        try:
            allowed = set("0123456789+-*/().% ")
            if not all(c in allowed for c in expression):
                raise ValueError
            result = eval(expression, {"__builtins__": {}}, {})  # nosec
            await ctx.send(embed=discord.Embed(
                description=f"`{expression}` = **{result}**", color=0x57F287))
        except ZeroDivisionError:
            await ctx.reply("Can't divide by zero.")
        except Exception:
            await ctx.reply("❌ Invalid expression.")

    @commands.command(name="urban")
    async def prefix_urban(self, ctx: commands.Context, *, term: str):
        rem = await check_cooldown(ctx.guild.id, ctx.author.id, "urban")
        if rem > 0:
            await ctx.reply(f"⏳ Wait **{rem:.1f}s**."); return
        set_cooldown_ts(ctx.guild.id, ctx.author.id, "urban")
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.urbandictionary.com/v0/define",
                             params={"term": term}) as r:
                if r.status != 200:
                    await ctx.reply("Urban Dictionary is down."); return
                data = await r.json()
        results = data.get("list", [])
        if not results:
            await ctx.reply(f"No definition found for **{term}**."); return
        top  = results[0]
        defn = top.get("definition","")[:800].replace("[","").replace("]","")
        ex   = top.get("example","")[:400].replace("[","").replace("]","")
        e = discord.Embed(title=f"📖 {top['word']}", description=defn, color=0x1D2439)
        if ex:
            e.add_field(name="Example", value=f"*{ex}*", inline=False)
        e.set_footer(text=f"👍 {top.get('thumbs_up',0)}  👎 {top.get('thumbs_down',0)}")
        await ctx.send(embed=e)

    @commands.command(name="topic")
    async def prefix_topic(self, ctx: commands.Context):
        await ctx.send(embed=discord.Embed(
            description=f"💬 {random.choice(TOPICS)}", color=0x5865F2))

    @commands.command(name="firstmessage")
    async def prefix_firstmessage(self, ctx: commands.Context,
                                   member: discord.Member = None):
        target = member or ctx.author
        async with ctx.typing():
            async for msg in ctx.channel.history(limit=None, oldest_first=True):
                if msg.author.id == target.id:
                    e = discord.Embed(
                        title=f"📜 First message by {target.display_name}",
                        description=msg.content[:500] or "*[no text]*",
                        color=0x5865F2, timestamp=msg.created_at)
                    e.set_thumbnail(url=target.display_avatar.url)
                    e.add_field(name="Jump", value=f"[Click here]({msg.jump_url})")
                    await ctx.send(embed=e)
                    return
            await ctx.reply("No messages found in this channel.")

    @commands.command(name="mybookmarks")
    async def prefix_mybookmarks(self, ctx: commands.Context):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT author_name,content,jump_url,timestamp FROM bookmarks"
                " WHERE user_id=? AND guild_id=? ORDER BY timestamp DESC LIMIT 10",
                (ctx.author.id, ctx.guild.id)
            ) as cur:
                rows = await cur.fetchall()
        if not rows:
            await ctx.reply("No bookmarks yet. React 🔖 to save a message."); return
        e = discord.Embed(title="🔖 Your Bookmarks", color=0xFFD700)
        for author, content, jump_url, ts in rows:
            e.add_field(
                name=f"{author} — {ts[:10]}",
                value=f"{content[:80]}{'...' if len(content)>80 else ''}\n[Jump]({jump_url})",
                inline=False)
        await ctx.send(embed=e)

async def setup(bot):
    await bot.add_cog(Fun(bot))
