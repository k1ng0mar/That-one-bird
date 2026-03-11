# cogs/info.py — userinfo, serverinfo, ping, help
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from cogs.utils import DB, get_warn_count

class Info(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── userinfo ──────────────────────────────────────────────
    @app_commands.command(name="userinfo", description="View info about a member")
    async def slash_userinfo(self, i: discord.Interaction, member: discord.Member = None):
        await self._userinfo(i.response.send_message, i.guild, member or i.user)

    @commands.command(name="userinfo")
    async def prefix_userinfo(self, ctx: commands.Context, member: discord.Member = None):
        await self._userinfo(ctx.send, ctx.guild, member or ctx.author)

    async def _userinfo(self, send_fn, guild: discord.Guild, target: discord.Member):
        warns = await get_warn_count(target.id, guild.id)
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND user_id=?",
                (guild.id, target.id)
            ) as cur:
                log_count = (await cur.fetchone())[0]
        roles = [r.mention for r in reversed(target.roles[1:])][:12]
        e = discord.Embed(title=str(target), color=target.color or discord.Color.blurple(),
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="ID",             value=str(target.id))
        e.add_field(name="Joined",         value=f"<t:{int(target.joined_at.timestamp())}:R>")
        e.add_field(name="Registered",     value=f"<t:{int(target.created_at.timestamp())}:R>")
        e.add_field(name="Active Warns",   value=str(warns))
        e.add_field(name="Total Mod Logs", value=str(log_count))
        e.add_field(name="Bot",            value="✅" if target.bot else "❌")
        e.add_field(name=f"Roles ({len(target.roles)-1})",
                    value=" ".join(roles) if roles else "None", inline=False)
        await send_fn(embed=e)

    # ── serverinfo ────────────────────────────────────────────
    @app_commands.command(name="serverinfo", description="View server info")
    async def slash_serverinfo(self, i: discord.Interaction):
        await self._serverinfo(i.response.send_message, i.guild)

    @commands.command(name="serverinfo")
    async def prefix_serverinfo(self, ctx: commands.Context):
        await self._serverinfo(ctx.send, ctx.guild)

    async def _serverinfo(self, send_fn, guild: discord.Guild):
        e = discord.Embed(title=guild.name, color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.add_field(name="Owner",    value=guild.owner.mention if guild.owner else "Unknown")
        e.add_field(name="Members",  value=str(guild.member_count))
        e.add_field(name="Channels", value=str(len(guild.channels)))
        e.add_field(name="Roles",    value=str(len(guild.roles)))
        e.add_field(name="Boosts",   value=str(guild.premium_subscription_count))
        e.add_field(name="Created",  value=f"<t:{int(guild.created_at.timestamp())}:R>")
        e.add_field(name="Verification",
                    value=str(guild.verification_level).replace("_"," ").title())
        await send_fn(embed=e)

    # ── ping ──────────────────────────────────────────────────
    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, i: discord.Interaction):
        await i.response.send_message(embed=discord.Embed(
            description=f"🏓 Pong! `{round(self.bot.latency*1000)}ms`", color=0x57F287))

    @commands.command(name="ping")
    async def prefix_ping(self, ctx: commands.Context):
        await ctx.send(embed=discord.Embed(
            description=f"🏓 Pong! `{round(self.bot.latency*1000)}ms`", color=0x57F287))

    # ── help ──────────────────────────────────────────────────
    @app_commands.command(name="help", description="Show all commands")
    async def slash_help(self, i: discord.Interaction):
        prefix = self.bot.prefix_cache.get(i.guild.id, "?") if i.guild else "?"
        e = await self._build_help(i.guild, prefix)
        await i.response.send_message(embed=e, ephemeral=True)

    @commands.command(name="help")
    async def prefix_help(self, ctx: commands.Context):
        prefix = self.bot.prefix_cache.get(ctx.guild.id, "?") if ctx.guild else "?"
        e = await self._build_help(ctx.guild, prefix)
        await ctx.send(embed=e)

    async def _build_help(self, guild: discord.Guild, prefix: str) -> discord.Embed:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT name, action_type FROM custom_commands WHERE guild_id=?",
                (guild.id,)
            ) as cur:
                custom = await cur.fetchall()

        e = discord.Embed(
            title="🐦 that one bird — Command Reference",
            description=f"Prefix: `{prefix}` · All commands available as `/slash` and `{prefix}prefix`",
            color=0x5865F2, timestamp=datetime.now(timezone.utc))

        e.add_field(name="⚙️ Settings (admin)", inline=False, value=
            f"`/setup` · `/setprefix` · `/setlogchannel <mod|messages|members|server>`\n"
            f"`/setwelcome` · `/setautorole` · `/setjail` · `/setdeadchatrole` · `/setdeadchatperm`\n"
            f"`/setstarboard` · `/setchapterchannel` · `/setcharacterchannel`\n"
            f"`/setcooldown` · `/setpermission` · `/setdisplay <public|ephemeral|timed>`\n"
            f"`/antiraidsettings` · `/antiraidtoggle` · `/setwarnthreshold <kick|ban|mute>`")

        e.add_field(name="🔨 Moderation (slash + prefix)", inline=False, value=
            f"`warn [@member] [reason] [expiry]` — reply to message = auto proof\n"
            f"`unwarn @member <id>` · `clearwarns [@member]` · `warns [@member]`\n"
            f"`history [@member]` — all mod actions against a user\n"
            f"`modlogs @mod` — actions by a moderator\n"
            f"`mute [@member] <mins> [reason]` · `unmute [@member]`\n"
            f"`kick [@member] [reason]` · `ban [@member] [reason]`\n"
            f"`tempban [@member] <dur> [reason]` · `jail [@member]` · `unjail [@member]`\n"
            f"`purge <1-100>` · `nick [@member] [name]` · `slowmode <sec>`\n"
            f"`lookup <user_id>` — fetch any user by ID")

        e.add_field(name="🎭 Roles", inline=False, value=
            f"`/role add/remove @member @role` · `{prefix}roleadd/roleremove`\n"
            f"`/role info @role` · `/role list` · `/role create <name> [color]`\n"
            f"`/role delete @role` · `/role color @role <#hex>`\n"
            f"`{prefix}roleinfo @role` · `{prefix}rolelist`")

        e.add_field(name="🛡️ Automod (admin)", inline=False, value=
            f"`/automod toggle` · `/automod addword/removeword/listwords`\n"
            f"`/automod setaction <delete_only|warn|mute>`\n"
            f"`/automod setmuteduration <mins>` · `/automod setwarnexpiry <dur>`")

        e.add_field(name="⚡ Triggers", inline=False, value=
            f"`/settrigger <trigger> <response> [contains|startswith]`\n"
            f"Response can be text, image URL, or GIF URL\n"
            f"`/listtriggers` · `/deletetrigger <id>`")

        e.add_field(name="🤖 Custom Commands", inline=False, value=
            f"`/addcommand <name> <message|ping|alias> <value>`\n"
            f"`/listcommands` · `/deletecommand <name>`\n"
            + (("\n".join(f"  `{prefix}{n}` [{t}]" for n, t in custom[:6]))
               if custom else "  *None set yet*"))

        e.add_field(name="🎉 Fun", inline=False, value=
            f"`meme` · `roast <target>` · `8ball <q>` · `deadchat`\n"
            f"`poll <q> | opt1 | opt2` · `remind <time> <msg>` · `snipe`\n"
            f"`afk [reason]` · `topic` · `coinflip` · `dice [sides]` · `calc <expr>`\n"
            f"`urban <term>` · `firstmessage [@member]`\n"
            f"`hug/slap/bite/punch @member` · `say/announce/pingrole`")

        e.add_field(name="🖼️ Images & Media", inline=False, value=
            f"`avatar [@member]` · `/banner [@member]` · `servericon`\n"
            f"`quote` *(prefix only — reply to a message)*\n"
            f"React 🔖 to any message to bookmark it · `mybookmarks`")

        e.add_field(name="⭐ Starboard", inline=False, value=
            f"React with the configured emoji (default ⭐) to star a message.\n"
            f"Reaches threshold → auto-posted with author ping + jump link.")

        e.add_field(name="📖 Blood Trials", inline=False, value=
            f"`/character <name>` · `{prefix}character <name>`\n"
            f"Chapters + characters auto-announced when published in Supabase.")

        e.add_field(name="ℹ️ Info", inline=False, value=
            f"`userinfo [@member]` · `serverinfo` · `ping`")

        e.add_field(name="💡 Tips", inline=False, value=
            f"• Reply to a message before `{prefix}warn/kick/ban/mute` to auto-attach proof\n"
            f"• No need to @mention a target if you're replying to their message\n"
            f"• Mention me or chat in `#ai-chat` to talk to Umar-bot (Groq)\n"
            f"• `/setdisplay` controls if any command response is public, ephemeral, or timed\n"
            f"• `/setwarnthreshold` controls auto-kick/ban/mute at warn counts")

        e.set_footer(text="that one bird 🐦 · Powered by Groq")
        return e

async def setup(bot):
    await bot.add_cog(Info(bot))
