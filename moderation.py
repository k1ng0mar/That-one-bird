# cogs/moderation.py — warn, mute, kick, ban, jail, purge, nick, slowmode, tempban

import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite
from datetime import datetime, timedelta, timezone

from cogs.utils import (
    DB, get_setting, log_action, add_warn, get_warn_count, get_all_warns,
    remove_warn_by_id, clear_all_warns, try_dm, fetch_member,
    parse_duration, check_command_perm, get_proof
)

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tempban_task.start()
        self.cleanup_warns_task.start()

    def cog_unload(self):
        self.tempban_task.cancel()
        self.cleanup_warns_task.cancel()

    # ── Background tasks ──────────────────────────────────────
    @tasks.loop(minutes=5)
    async def tempban_task(self):
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT user_id, guild_id FROM tempbans WHERE unban_at <= ?", (now,)
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                await db.execute("DELETE FROM tempbans WHERE unban_at <= ?", (now,))
                await db.commit()
        for uid, gid in rows:
            try:
                guild = self.bot.get_guild(gid) or await self.bot.fetch_guild(gid)
                await guild.unban(discord.Object(id=uid), reason="Tempban expired")
                log_ch_id = await get_setting(gid, 'log_mod_id')
                if log_ch_id:
                    ch = self.bot.get_channel(log_ch_id)
                    if ch:
                        e = discord.Embed(title="🔓 Tempban Expired",
                                          description=f"<@{uid}> has been automatically unbanned.",
                                          color=0x57F287, timestamp=datetime.now(timezone.utc))
                        await ch.send(embed=e)
            except Exception as ex:
                print(f"Tempban unban error: {ex}")

    @tasks.loop(hours=1)
    async def cleanup_warns_task(self):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM warns WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),)
            )
            await db.commit()

    # ── Shared warn logic ─────────────────────────────────────
    async def do_warn(self, guild, moderator, member: discord.Member,
                      reason: str, expires_at=None):
        count = await add_warn(member.id, guild.id, moderator.id, reason, expires_at)
        await log_action(self.bot, "Warn", member, moderator, reason, guild.id)

        dm = discord.Embed(title="⚠️ You have been warned", color=0xFFAA00,
                           timestamp=datetime.now(timezone.utc))
        dm.add_field(name="Server",      value=guild.name)
        dm.add_field(name="Reason",      value=reason or "None")
        dm.add_field(name="Total Warns", value=str(count), inline=False)
        if expires_at:
            dm.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>")
        await try_dm(member, dm)

        autokicked = False
        if count >= 3:
            kd = discord.Embed(title="👢 Auto-kicked", color=0xFF4444)
            kd.add_field(name="Reason", value="Reached 3 warnings")
            await try_dm(member, kd)
            await member.kick(reason="3+ warns")
            await log_action(self.bot, "Auto-kick (3 warns)", member,
                             self.bot.user, guild_id=guild.id)
            autokicked = True
        return count, autokicked

    # ─────────────────────────────────────────────────────────
    #  SLASH COMMANDS
    # ─────────────────────────────────────────────────────────

    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Member to warn", reason="Reason",
                           expires_in="Optional expiry e.g. 7d 24h 30m")
    async def slash_warn(self, interaction: discord.Interaction,
                         member: discord.Member, reason: str = None,
                         expires_in: str = None):
        delta      = parse_duration(expires_in) if expires_in else None
        expires_at = datetime.now(timezone.utc) + delta if delta else None
        count, kicked = await self.do_warn(interaction.guild, interaction.user,
                                           member, reason, expires_at)
        e = discord.Embed(title="⚠️ Member Warned", color=0xFFAA00,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member",      value=member.mention)
        e.add_field(name="By",          value=interaction.user.mention)
        e.add_field(name="Reason",      value=reason or "None", inline=False)
        e.add_field(name="Total Warns", value=str(count))
        if expires_at:
            e.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>")
        await interaction.response.send_message(embed=e)
        if kicked:
            await interaction.followup.send(embed=discord.Embed(
                description=f"👢 Auto-kicked {member.mention} for reaching 3 warns.",
                color=0xFF4444))

    @app_commands.command(name="unwarn", description="Remove a warn by ID")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unwarn(self, interaction: discord.Interaction,
                           member: discord.Member, warn_id: int):
        if not await remove_warn_by_id(warn_id, interaction.guild.id):
            await interaction.response.send_message("❌ Warn ID not found.", ephemeral=True)
            return
        await log_action(self.bot, "Remove Warn", member, interaction.user,
                         f"Warn #{warn_id}", interaction.guild.id)
        await interaction.response.send_message(embed=discord.Embed(
            description=f"✅ Removed warn `#{warn_id}` from {member.mention}.",
            color=0x57F287))

    @app_commands.command(name="clearwarns", description="Clear all warns for a member")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_clearwarns(self, interaction: discord.Interaction, member: discord.Member):
        count = await clear_all_warns(member.id, interaction.guild.id)
        await log_action(self.bot, "Clear Warns", member, interaction.user,
                         guild_id=interaction.guild.id)
        await interaction.response.send_message(embed=discord.Embed(
            description=f"🗑️ Cleared **{count}** warn(s) from {member.mention}.",
            color=0x57F287))

    @app_commands.command(name="warns", description="Check warns for a member")
    async def slash_warns(self, interaction: discord.Interaction,
                          member: discord.Member = None):
        target = member or interaction.user
        rows   = await get_all_warns(target.id, interaction.guild.id)
        e = discord.Embed(title=f"⚠️ Warns — {target.display_name}",
                          color=0xFFAA00, timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        if not rows:
            e.description = "No active warns ✅"
        else:
            for wid, mid, reason, exp, ts in rows:
                mod    = interaction.guild.get_member(mid)
                expiry = (f"\nExpires: <t:{int(datetime.fromisoformat(exp).timestamp())}:R>"
                          if exp else "")
                e.add_field(
                    name=f"Warn #{wid} — {ts[:10]}",
                    value=f"By: {mod.mention if mod else f'<@{mid}>'}\n"
                          f"Reason: {reason or 'None'}{expiry}",
                    inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="modlogs", description="Show mod actions by a moderator")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_modlogs(self, interaction: discord.Interaction,
                            moderator: discord.Member):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action,user_id,reason,timestamp FROM mod_logs "
                "WHERE guild_id=? AND moderator_id=? ORDER BY timestamp DESC LIMIT 20",
                (interaction.guild.id, moderator.id)
            ) as cur:
                rows = await cur.fetchall()
        e = discord.Embed(title=f"📋 Mod Logs — {moderator.display_name}",
                          color=0x5865F2, timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=moderator.display_avatar.url)
        e.description = "No actions found." if not rows else None
        for action, uid, reason, ts in rows:
            e.add_field(name=f"{action} — {ts[:10]}",
                        value=f"User: <@{uid}>\nReason: {reason or 'None'}",
                        inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="mute", description="Timeout a member")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
    async def slash_mute(self, interaction: discord.Interaction,
                         member: discord.Member, minutes: int, reason: str = None):
        await interaction.response.defer()
        member = await fetch_member(self.bot, interaction.guild_id, member.id)
        until  = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await member.edit(timed_out_until=until, reason=reason)
        await log_action(self.bot, f"Mute {minutes}min", member,
                         interaction.user, reason, interaction.guild_id)
        e = discord.Embed(title="🔇 Member Muted", color=0xFF8800,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member",   value=member.mention)
        e.add_field(name="By",       value=interaction.user.mention)
        e.add_field(name="Duration", value=f"{minutes} min")
        e.add_field(name="Reason",   value=reason or "None", inline=False)
        await interaction.followup.send(embed=e)
        dm = discord.Embed(title="🔇 You have been muted", color=0xFF8800)
        dm.add_field(name="Server",   value=member.guild.name)
        dm.add_field(name="Duration", value=f"{minutes} min")
        dm.add_field(name="Reason",   value=reason or "None")
        await try_dm(member, dm)

    @app_commands.command(name="unmute", description="Remove a member's timeout")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unmute(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        member = await fetch_member(self.bot, interaction.guild_id, member.id)
        await member.edit(timed_out_until=None)
        await log_action(self.bot, "Unmute", member, interaction.user,
                         guild_id=interaction.guild_id)
        await interaction.followup.send(embed=discord.Embed(
            description=f"🔊 Unmuted {member.mention}.", color=0x57F287))

    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.default_permissions(kick_members=True)
    async def slash_kick(self, interaction: discord.Interaction,
                         member: discord.Member, reason: str = None):
        dm = discord.Embed(title="👢 You have been kicked", color=0xFF4444)
        dm.add_field(name="Server", value=interaction.guild.name)
        dm.add_field(name="Reason", value=reason or "None")
        await try_dm(member, dm)
        await member.kick(reason=reason)
        await log_action(self.bot, "Kick", member, interaction.user,
                         reason, interaction.guild.id)
        e = discord.Embed(title="👢 Member Kicked", color=0xFF4444,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="By",     value=interaction.user.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.default_permissions(ban_members=True)
    async def slash_ban(self, interaction: discord.Interaction,
                        member: discord.Member, reason: str = None):
        dm = discord.Embed(title="🔨 You have been banned", color=0xCC0000)
        dm.add_field(name="Server", value=interaction.guild.name)
        dm.add_field(name="Reason", value=reason or "None")
        await try_dm(member, dm)
        await member.ban(reason=reason)
        await log_action(self.bot, "Ban", member, interaction.user,
                         reason, interaction.guild.id)
        e = discord.Embed(title="🔨 Member Banned", color=0xCC0000,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="By",     value=interaction.user.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="tempban", description="Temporarily ban a member")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(member="Member", duration="e.g. 1d 12h 30m", reason="Reason")
    async def slash_tempban(self, interaction: discord.Interaction,
                            member: discord.Member, duration: str, reason: str = None):
        delta = parse_duration(duration)
        if not delta:
            await interaction.response.send_message("❌ Invalid duration.", ephemeral=True)
            return
        unban_at = datetime.now(timezone.utc) + delta
        dm = discord.Embed(title="🔨 You have been temporarily banned", color=0xCC0000)
        dm.add_field(name="Server",   value=interaction.guild.name)
        dm.add_field(name="Duration", value=duration)
        dm.add_field(name="Reason",   value=reason or "None")
        await try_dm(member, dm)
        await member.ban(reason=reason)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO tempbans (user_id,guild_id,unban_at) VALUES (?,?,?)",
                (member.id, interaction.guild.id, unban_at.isoformat())
            )
            await db.commit()
        await log_action(self.bot, f"Tempban ({duration})", member,
                         interaction.user, reason, interaction.guild.id)
        e = discord.Embed(title="🔨 Member Tempbanned", color=0xCC0000,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member",   value=member.mention)
        e.add_field(name="Duration", value=duration)
        e.add_field(name="Unban At", value=f"<t:{int(unban_at.timestamp())}:R>")
        e.add_field(name="Reason",   value=reason or "None", inline=False)
        await interaction.response.send_message(embed=e)

    @app_commands.command(name="jail", description="Jail a member (strips roles, limits to jail channel)")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_jail(self, interaction: discord.Interaction,
                         member: discord.Member, reason: str = None):
        await interaction.response.defer()
        await self._do_jail(interaction.guild, interaction.user, member, reason)
        e = discord.Embed(title="🔒 Member Jailed", color=0xFF6600,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="By",     value=interaction.user.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await interaction.followup.send(embed=e)

    @app_commands.command(name="unjail", description="Release a member from jail")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unjail(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        await self._do_unjail(interaction.guild, interaction.user, member)
        await interaction.followup.send(embed=discord.Embed(
            description=f"🔓 Released {member.mention} from jail.", color=0x57F287))

    @app_commands.command(name="purge", description="Delete messages (max 100)")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_purge(self, interaction: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            await interaction.response.send_message("❌ Between 1–100.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        deleted = await interaction.channel.purge(limit=amount)
        await log_action(self.bot, f"Purge ({len(deleted)} msgs)",
                         interaction.user, interaction.user,
                         guild_id=interaction.guild_id)
        await interaction.followup.send(
            f"🗑️ Deleted **{len(deleted)}** message(s).", ephemeral=True)

    @app_commands.command(name="nick", description="Change a member's nickname")
    @app_commands.default_permissions(manage_nicknames=True)
    async def slash_nick(self, interaction: discord.Interaction,
                         member: discord.Member, nickname: str = None):
        old = member.display_name
        await member.edit(nick=nickname)
        await log_action(self.bot, "Nick Change", member, interaction.user,
                         f"{old} → {nickname or 'reset'}", interaction.guild.id)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"✅ Changed {member.mention}'s nick to **{nickname or 'reset'}**.",
                color=0x57F287))

    @app_commands.command(name="slowmode", description="Set channel slowmode (0 to disable)")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_slowmode(self, interaction: discord.Interaction, seconds: int):
        await interaction.channel.edit(slowmode_delay=seconds)
        await interaction.response.send_message(
            embed=discord.Embed(
                description=f"⏱️ Slowmode set to **{seconds}s**." if seconds else "⏱️ Slowmode disabled.",
                color=0x57F287))

    # ── Jail helpers ──────────────────────────────────────────
    async def _do_jail(self, guild, moderator, member: discord.Member, reason: str):
        jail_role_id = await get_setting(guild.id, 'jail_role_id')
        if not jail_role_id:
            return
        jail_role = guild.get_role(jail_role_id)
        if not jail_role:
            return
        role_ids = [r.id for r in member.roles if not r.is_default()]
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM jailed_roles WHERE user_id=? AND guild_id=?",
                (member.id, guild.id))
            for rid in role_ids:
                await db.execute(
                    "INSERT INTO jailed_roles (user_id,guild_id,role_id) VALUES (?,?,?)",
                    (member.id, guild.id, rid))
            await db.commit()
        roles_to_remove = [r for r in member.roles if not r.is_default()]
        await member.remove_roles(*roles_to_remove, reason="Jailed")
        await member.add_roles(jail_role, reason=reason)
        await log_action(self.bot, "Jail", member, moderator, reason, guild.id)
        dm = discord.Embed(title="🔒 You have been jailed", color=0xFF6600)
        dm.add_field(name="Server", value=guild.name)
        dm.add_field(name="Reason", value=reason or "None")
        await try_dm(member, dm)

    async def _do_unjail(self, guild, moderator, member: discord.Member):
        jail_role_id = await get_setting(guild.id, 'jail_role_id')
        if jail_role_id:
            jail_role = guild.get_role(jail_role_id)
            if jail_role and jail_role in member.roles:
                await member.remove_roles(jail_role, reason="Unjailed")
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT role_id FROM jailed_roles WHERE user_id=? AND guild_id=?",
                (member.id, guild.id)
            ) as cur:
                rows = await cur.fetchall()
            await db.execute(
                "DELETE FROM jailed_roles WHERE user_id=? AND guild_id=?",
                (member.id, guild.id))
            await db.commit()
        roles_to_add = [guild.get_role(r[0]) for r in rows
                        if guild.get_role(r[0])]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Unjailed")
        await log_action(self.bot, "Unjail", member, moderator, guild_id=guild.id)

    # ─────────────────────────────────────────────────────────
    #  PREFIX COMMANDS
    # ─────────────────────────────────────────────────────────

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def prefix_warn(self, ctx: commands.Context,
                          member: discord.Member, *, args: str = ""):
        tokens     = args.split()
        expires_at = None
        if tokens:
            d = parse_duration(tokens[-1])
            if d:
                expires_at = datetime.now(timezone.utc) + d
                tokens     = tokens[:-1]
        reason = " ".join(tokens) or None
        proof  = await get_proof(ctx)
        full   = (reason or "No reason") + (f"\n{proof}" if proof else "")
        count, kicked = await self.do_warn(ctx.guild, ctx.author,
                                           member, full, expires_at)
        e = discord.Embed(title="⚠️ Member Warned", color=0xFFAA00,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member",      value=member.mention)
        e.add_field(name="By",          value=ctx.author.mention)
        e.add_field(name="Reason",      value=full, inline=False)
        e.add_field(name="Total Warns", value=str(count))
        if expires_at:
            e.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>")
        await ctx.send(embed=e)
        if kicked:
            await ctx.send(embed=discord.Embed(
                description=f"👢 Auto-kicked {member.mention} (3 warns).",
                color=0xFF4444))

    @commands.command(name="unwarn")
    @commands.has_permissions(moderate_members=True)
    async def prefix_unwarn(self, ctx: commands.Context,
                            member: discord.Member, warn_id: int):
        if not await remove_warn_by_id(warn_id, ctx.guild.id):
            await ctx.send("❌ Warn ID not found.")
            return
        await log_action(self.bot, "Remove Warn", member, ctx.author,
                         f"Warn #{warn_id}", ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Removed warn `#{warn_id}` from {member.mention}.",
            color=0x57F287))

    @commands.command(name="clearwarns")
    @commands.has_permissions(moderate_members=True)
    async def prefix_clearwarns(self, ctx: commands.Context, member: discord.Member):
        count = await clear_all_warns(member.id, ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"🗑️ Cleared **{count}** warn(s) from {member.mention}.",
            color=0x57F287))

    @commands.command(name="warns")
    async def prefix_warns(self, ctx: commands.Context, member: discord.Member = None):
        target = member or ctx.author
        rows   = await get_all_warns(target.id, ctx.guild.id)
        e = discord.Embed(title=f"⚠️ Warns — {target.display_name}", color=0xFFAA00)
        if not rows:
            e.description = "No active warns ✅"
        else:
            for wid, mid, reason, exp, ts in rows:
                mod = ctx.guild.get_member(mid)
                e.add_field(name=f"Warn #{wid}", value=f"Reason: {reason or 'None'}", inline=False)
        await ctx.send(embed=e)

    @commands.command(name="modlogs")
    @commands.has_permissions(moderate_members=True)
    async def prefix_modlogs(self, ctx: commands.Context, moderator: discord.Member):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action,user_id,reason,timestamp FROM mod_logs "
                "WHERE guild_id=? AND moderator_id=? ORDER BY timestamp DESC LIMIT 20",
                (ctx.guild.id, moderator.id)
            ) as cur:
                rows = await cur.fetchall()
        e = discord.Embed(title=f"📋 Mod Logs — {moderator.display_name}", color=0x5865F2)
        e.description = "No actions." if not rows else None
        for action, uid, reason, ts in rows:
            e.add_field(name=f"{action} — {ts[:10]}",
                        value=f"<@{uid}> — {reason or 'None'}", inline=False)
        await ctx.send(embed=e)

    @commands.command(name="mute")
    @commands.has_permissions(moderate_members=True)
    async def prefix_mute(self, ctx: commands.Context, member: discord.Member,
                          minutes: int, *, reason: str = None):
        member = await fetch_member(self.bot, ctx.guild.id, member.id)
        until  = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await member.edit(timed_out_until=until, reason=reason)
        await log_action(self.bot, f"Mute {minutes}min", member,
                         ctx.author, reason, ctx.guild.id)
        e = discord.Embed(title="🔇 Member Muted", color=0xFF8800)
        e.add_field(name="Member",   value=member.mention)
        e.add_field(name="Duration", value=f"{minutes} min")
        e.add_field(name="Reason",   value=reason or "None", inline=False)
        await ctx.send(embed=e)

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def prefix_unmute(self, ctx: commands.Context, member: discord.Member):
        member = await fetch_member(self.bot, ctx.guild.id, member.id)
        await member.edit(timed_out_until=None)
        await log_action(self.bot, "Unmute", member, ctx.author, guild_id=ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"🔊 Unmuted {member.mention}.", color=0x57F287))

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def prefix_kick(self, ctx: commands.Context, member: discord.Member,
                          *, reason: str = None):
        proof = await get_proof(ctx)
        full  = (reason or "No reason") + (f"\n{proof}" if proof else "")
        dm    = discord.Embed(title="👢 You have been kicked", color=0xFF4444)
        dm.add_field(name="Server", value=ctx.guild.name)
        dm.add_field(name="Reason", value=full)
        await try_dm(member, dm)
        await member.kick(reason=full)
        await log_action(self.bot, "Kick", member, ctx.author, full, ctx.guild.id)
        e = discord.Embed(title="👢 Member Kicked", color=0xFF4444)
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="Reason", value=full, inline=False)
        await ctx.send(embed=e)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def prefix_ban(self, ctx: commands.Context, member: discord.Member,
                         *, reason: str = None):
        proof = await get_proof(ctx)
        full  = (reason or "No reason") + (f"\n{proof}" if proof else "")
        dm    = discord.Embed(title="🔨 You have been banned", color=0xCC0000)
        dm.add_field(name="Server", value=ctx.guild.name)
        dm.add_field(name="Reason", value=full)
        await try_dm(member, dm)
        await member.ban(reason=full)
        await log_action(self.bot, "Ban", member, ctx.author, full, ctx.guild.id)
        e = discord.Embed(title="🔨 Member Banned", color=0xCC0000)
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="Reason", value=full, inline=False)
        await ctx.send(embed=e)

    @commands.command(name="tempban")
    @commands.has_permissions(ban_members=True)
    async def prefix_tempban(self, ctx: commands.Context, member: discord.Member,
                             duration: str, *, reason: str = None):
        delta = parse_duration(duration)
        if not delta:
            await ctx.send("❌ Invalid duration. Use e.g. `1d`, `12h`, `30m`.")
            return
        unban_at = datetime.now(timezone.utc) + delta
        proof = await get_proof(ctx)
        full  = (reason or "No reason") + (f"\n{proof}" if proof else "")
        dm    = discord.Embed(title="🔨 Temporarily Banned", color=0xCC0000)
        dm.add_field(name="Server",   value=ctx.guild.name)
        dm.add_field(name="Duration", value=duration)
        dm.add_field(name="Reason",   value=full)
        await try_dm(member, dm)
        await member.ban(reason=full)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO tempbans (user_id,guild_id,unban_at) VALUES (?,?,?)",
                (member.id, ctx.guild.id, unban_at.isoformat()))
            await db.commit()
        await log_action(self.bot, f"Tempban ({duration})", member,
                         ctx.author, full, ctx.guild.id)
        e = discord.Embed(title="🔨 Tempbanned", color=0xCC0000)
        e.add_field(name="Member",   value=member.mention)
        e.add_field(name="Duration", value=duration)
        e.add_field(name="Unban",    value=f"<t:{int(unban_at.timestamp())}:R>")
        await ctx.send(embed=e)

    @commands.command(name="jail")
    @commands.has_permissions(moderate_members=True)
    async def prefix_jail(self, ctx: commands.Context, member: discord.Member,
                          *, reason: str = None):
        await self._do_jail(ctx.guild, ctx.author, member, reason)
        e = discord.Embed(title="🔒 Member Jailed", color=0xFF6600)
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await ctx.send(embed=e)

    @commands.command(name="unjail")
    @commands.has_permissions(moderate_members=True)
    async def prefix_unjail(self, ctx: commands.Context, member: discord.Member):
        await self._do_unjail(ctx.guild, ctx.author, member)
        await ctx.send(embed=discord.Embed(
            description=f"🔓 Released {member.mention} from jail.", color=0x57F287))

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge(self, ctx: commands.Context, amount: int):
        if not 1 <= amount <= 100:
            await ctx.send("❌ Between 1–100.")
            return
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=amount)
        import asyncio
        m = await ctx.send(f"🗑️ Deleted **{len(deleted)}** message(s).")
        await asyncio.sleep(5)
        await m.delete()

    @commands.command(name="nick")
    @commands.has_permissions(manage_nicknames=True)
    async def prefix_nick(self, ctx: commands.Context, member: discord.Member,
                          *, nickname: str = None):
        old = member.display_name
        await member.edit(nick=nickname)
        await log_action(self.bot, "Nick Change", member, ctx.author,
                         f"{old} → {nickname or 'reset'}", ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"✅ Nick → **{nickname or 'reset'}** for {member.mention}.",
            color=0x57F287))

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def prefix_slowmode(self, ctx: commands.Context, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(embed=discord.Embed(
            description=f"⏱️ Slowmode set to **{seconds}s**." if seconds else "⏱️ Slowmode disabled.",
            color=0x57F287))

    # ── Error handling ────────────────────────────────────────
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You don't have permission.")
        elif isinstance(error, commands.MemberNotFound):
            await ctx.reply("❌ Member not found.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"❌ Missing: `{error.param.name}`")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(f"❌ Bad argument: {error}")
        elif isinstance(error, commands.CommandNotFound):
            pass
        else:
            print(f"Command error [{ctx.command}]: {error}")

    @app_commands.error
    async def on_app_command_error(self, interaction, error):
        msg = "❌ Something went wrong."
        if isinstance(error, app_commands.MissingPermissions):
            msg = "❌ You don't have permission."
        try:
            await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            await interaction.followup.send(msg, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Moderation(bot))
