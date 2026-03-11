# cogs/info.py — userinfo, serverinfo, ping, help

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timezone

from cogs.utils import DB, get_warn_count

class Info(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /userinfo ─────────────────────────────────────────────
    @app_commands.command(name="userinfo", description="View info about a member")
    async def slash_userinfo(self, interaction: discord.Interaction,
                             member: discord.Member = None):
        await self._userinfo(interaction.response.send_message,
                             interaction.guild, member or interaction.user)

    @commands.command(name="userinfo")
    async def prefix_userinfo(self, ctx: commands.Context, member: discord.Member = None):
        await self._userinfo(ctx.send, ctx.guild, member or ctx.author)

    async def _userinfo(self, send_fn, guild, target: discord.Member):
        warns = await get_warn_count(target.id, guild.id)
        roles = [r.mention for r in target.roles[1:]][:10]
        e = discord.Embed(title=str(target), color=target.color,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="ID",           value=str(target.id))
        e.add_field(name="Joined",       value=f"<t:{int(target.joined_at.timestamp())}:R>")
        e.add_field(name="Registered",   value=f"<t:{int(target.created_at.timestamp())}:R>")
        e.add_field(name="Active Warns", value=str(warns))
        e.add_field(name=f"Roles ({len(target.roles)-1})",
                    value=" ".join(roles) or "None", inline=False)
        await send_fn(embed=e)

    # ── /serverinfo ───────────────────────────────────────────
    @app_commands.command(name="serverinfo", description="View server info")
    async def slash_serverinfo(self, interaction: discord.Interaction):
        await self._serverinfo(interaction.response.send_message, interaction.guild)

    @commands.command(name="serverinfo")
    async def prefix_serverinfo(self, ctx: commands.Context):
        await self._serverinfo(ctx.send, ctx.guild)

    async def _serverinfo(self, send_fn, guild: discord.Guild):
        e = discord.Embed(title=guild.name, color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        if guild.icon:
            e.set_thumbnail(url=guild.icon.url)
        e.add_field(name="Owner",   value=guild.owner.mention if guild.owner else "Unknown")
        e.add_field(name="Members", value=str(guild.member_count))
        e.add_field(name="Channels",value=str(len(guild.channels)))
        e.add_field(name="Roles",   value=str(len(guild.roles)))
        e.add_field(name="Boosts",  value=str(guild.premium_subscription_count))
        e.add_field(name="Created", value=f"<t:{int(guild.created_at.timestamp())}:R>")
        await send_fn(embed=e)

    # ── /ping ─────────────────────────────────────────────────
    @app_commands.command(name="ping", description="Check bot latency")
    async def slash_ping(self, interaction: discord.Interaction):
        await interaction.response.send_message(embed=discord.Embed(
            description=f"🏓 Pong! `{round(self.bot.latency * 1000)}ms`",
            color=0x57F287))

    @commands.command(name="ping")
    async def prefix_ping(self, ctx: commands.Context):
        await ctx.send(embed=discord.Embed(
            description=f"🏓 Pong! `{round(self.bot.latency * 1000)}ms`",
            color=0x57F287))

    # ── /help ─────────────────────────────────────────────────
    @app_commands.command(name="help", description="Show all commands")
    async def slash_help(self, interaction: discord.Interaction):
        await self._help(interaction.response.send_message,
                         interaction.guild, ephemeral=True)

    @commands.command(name="help")
    async def prefix_help(self, ctx: commands.Context):
        await self._help(ctx.send, ctx.guild)

    async def _help(self, send_fn, guild: discord.Guild, ephemeral: bool = False):
        # Get prefix for this guild
        prefix = self.bot.prefix_cache.get(guild.id, "?") if guild else "?"

        # Load custom commands
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT name, action_type FROM custom_commands WHERE guild_id=?",
                (guild.id,)
            ) as cur:
                custom = await cur.fetchall()

        # Load command permissions summary
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT command, role_id FROM command_perms WHERE guild_id=?",
                (guild.id,)
            ) as cur:
                perms = {row[0]: row[1] for row in await cur.fetchall()}

        def perm_note(cmd: str) -> str:
            rid = perms.get(cmd)
            if rid is None: return ""
            if rid == 0: return " *(everyone)*"
            role = guild.get_role(rid)
            return f" *({role.name if role else 'restricted'})*"

        e = discord.Embed(
            title="🐦 that one bird — Full Command List",
            description=f"Prefix: `{prefix}` — all commands available as `/slash` and `{prefix}prefix`",
            color=0x5865F2,
            timestamp=datetime.now(timezone.utc)
        )

        e.add_field(name="⚙️ Settings *(admin only)*", inline=False, value=
            f"`/setup` — view all current settings\n"
            f"`/setprefix` — change command prefix\n"
            f"`/setlogchannel <category> <channel>` — categories: mod, messages, members, server\n"
            f"`/setwelcome <channel> <message>` — vars: `{{user}}` `{{name}}` `{{server}}` `{{count}}`\n"
            f"`/setautorole <role>` — auto-assign role on join\n"
            f"`/setjail <channel> <role>` — configure jail system\n"
            f"`/setdeadchatrole` / `/setdeadchatperm` — deadchat config\n"
            f"`/setstarboard <channel> <emoji> <threshold>` — starboard config\n"
            f"`/setchapterchannel` / `/setcharacterchannel` — Blood Trials announcements\n"
            f"`/setcooldown <command> <seconds>` — override cooldowns\n"
            f"`/setpermission <command> <role> <silent>` — per-command access control\n"
            f"`/antiraidsettings` / `/antiraidtoggle` — anti-raid config"
        )

        e.add_field(name="🔨 Moderation *(slash + prefix)*", inline=False, value=
            f"`warn @member [reason] [expiry: 7d/24h/30m]`{perm_note('warn')} — warn; reply to attach proof\n"
            f"`unwarn @member <warn_id>`{perm_note('unwarn')} — remove a specific warn\n"
            f"`clearwarns @member`{perm_note('clearwarns')} — clear all warns\n"
            f"`warns [@member]` — view active warns with IDs\n"
            f"`modlogs @moderator` — last 20 actions by a mod\n"
            f"`mute @member <minutes> [reason]`{perm_note('mute')}\n"
            f"`unmute @member`{perm_note('unmute')}\n"
            f"`kick @member [reason]`{perm_note('kick')} — reply to attach proof\n"
            f"`ban @member [reason]`{perm_note('ban')} — reply to attach proof\n"
            f"`tempban @member <duration> [reason]`{perm_note('tempban')} — auto-unbans after duration\n"
            f"`jail @member [reason]`{perm_note('jail')} — strips roles, limits to jail channel\n"
            f"`unjail @member`{perm_note('unjail')} — restores all original roles\n"
            f"`purge <1–100>`{perm_note('purge')}\n"
            f"`nick @member [nickname]`{perm_note('nick')} — change/reset nickname\n"
            f"`slowmode <seconds>`{perm_note('slowmode')} — 0 to disable"
        )

        e.add_field(name="🛡️ Automod *(admin)*", inline=False, value=
            f"`/automod addword <word>` — add to filter\n"
            f"`/automod removeword <word>` — remove from filter\n"
            f"`/automod listwords` — view all filtered words\n"
            f"`/automod setaction <delete_only|warn|mute>` — action on trigger\n"
            f"`/automod setmuteduration <minutes>` — mute duration\n"
            f"`/automod setwarnexpiry <e.g. 7d>` — warn expiry\n"
            f"`/automod toggle` — enable/disable automod"
        )

        e.add_field(name="⚡ Triggers *(manage messages)*", inline=False, value=
            f"`/settrigger <trigger> <response> [match_type]` — contains or startswith\n"
            f"Response can be text, image URL, or GIF URL (auto-detected)\n"
            f"`/listtriggers` — view all with IDs\n"
            f"`/deletetrigger <id>` — remove a trigger"
        )

        e.add_field(name="🤖 Custom Commands *(manage guild)*", inline=False, value=
            f"`/addcommand <name> <type> <value>` — types: message, ping, alias\n"
            f"`/listcommands` — view all custom commands\n"
            f"`/deletecommand <name>` — remove a custom command\n"
            f"**Alias example:** `addcommand myban alias ban` → `{prefix}myban` runs ban\n"
            + (("\n".join(f"  `{prefix}{n}` [{t}]" for n, t in custom[:8]))
               if custom else "  *No custom commands set yet.*")
        )

        e.add_field(name="📢 Announcements", inline=False, value=
            f"`say <message> [@role]`{perm_note('say')}\n"
            f"`announce <#channel> <message> [@role]`{perm_note('announce')}\n"
            f"`pingrole <@role>`{perm_note('pingrole')}\n"
            f"`deadchat`{perm_note('deadchat')} — revive chat (configurable cooldown, default 1h)"
        )

        e.add_field(name="🎉 Fun", inline=False, value=
            f"`meme`{perm_note('meme')} — random meme (10s cooldown)\n"
            f"`roast <target>`{perm_note('roast')} — Gemini roast (15s cooldown)\n"
            f"`8ball <question>`{perm_note('8ball')} — magic 8-ball\n"
            f"`poll <question> | <opt1> | <opt2> [| opt3] [| opt4]`{perm_note('poll')}\n"
            f"`remind <time> <message>` — e.g. `{prefix}remind 30m Do homework`\n"
            f"`snipe` — last 3 deleted messages\n"
            f"`afk [reason]` — mark yourself AFK\n"
            f"`hug/slap/bite/punch/kick_fun @member` — anime GIF interactions"
        )

        e.add_field(name="⭐ Starboard", inline=False, value=
            f"React to a message with the configured emoji to star it.\n"
            f"Reaches the threshold → auto-posted in starboard channel.\n"
            f"Includes original message, parent if reply, and jump link."
        )

        e.add_field(name="📖 Blood Trials", inline=False, value=
            f"`/character <name>` — look up a character\n"
            f"New chapters + characters are auto-announced when published."
        )

        e.add_field(name="ℹ️ Info", inline=False, value=
            f"`userinfo [@member]` — member details + warn count\n"
            f"`serverinfo` — server stats\n"
            f"`ping` — bot latency"
        )

        e.add_field(name="💡 Tips", inline=False, value=
            f"• Reply to a message before `{prefix}warn/kick/ban` to auto-attach it as proof\n"
            f"• Admins can restrict any command to a role via `/setpermission`\n"
            f"• All mod actions are logged to the configured log channels\n"
            f"• Gemini responds when mentioned or in any channel named `#ai-chat`"
        )

        e.set_footer(text="that one bird 🐦 | All commands work as both slash and prefix")

        kwargs = {"embed": e}
        if ephemeral:
            kwargs["ephemeral"] = True
        await send_fn(**kwargs)

async def setup(bot):
    await bot.add_cog(Info(bot))
