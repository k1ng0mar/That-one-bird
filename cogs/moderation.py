# cogs/moderation.py — full moderation suite
import asyncio
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands, tasks
import aiosqlite

from cogs.utils import (
    DB, get_setting, log_action,
    add_warn, get_warn_count, get_all_warns,
    remove_warn_by_id, clear_all_warns,
    try_dm, fetch_member, parse_duration,
    get_proof, get_reply_target
)

# ── DM embed factory ──────────────────────────────────────────
def dm_embed(title: str, color: int, fields: list[tuple]) -> discord.Embed:
    e = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
    for name, value in fields:
        e.add_field(name=name, value=str(value) if value else "None", inline=False)
    return e

class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.tempban_task.start()
        self.cleanup_warns_task.start()
        self.unmute_notify_task.start()

    def cog_unload(self):
        self.tempban_task.cancel()
        self.cleanup_warns_task.cancel()
        self.unmute_notify_task.cancel()

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
                user = await self.bot.fetch_user(uid)
                await try_dm(user, dm_embed(
                    "🔓 You have been unbanned",
                    0x57F287,
                    [("Server", guild.name), ("Reason", "Temporary ban expired")]
                ))
                ch_id = await get_setting(gid, 'log_mod_id')
                if ch_id:
                    ch = self.bot.get_channel(ch_id)
                    if ch:
                        e = discord.Embed(
                            title="🔓 Tempban Expired",
                            description=f"<@{uid}> auto-unbanned.",
                            color=0x57F287, timestamp=datetime.now(timezone.utc))
                        await ch.send(embed=e)
            except Exception as ex:
                print(f"Tempban unban error: {ex}")

    @tasks.loop(hours=1)
    async def cleanup_warns_task(self):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM warns WHERE expires_at IS NOT NULL AND expires_at <= ?",
                (datetime.now(timezone.utc).isoformat(),))
            await db.commit()

    @tasks.loop(minutes=1)
    async def unmute_notify_task(self):
        """DM users when their mute/timeout expires."""
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT user_id, guild_id FROM mute_tracking"
                " WHERE unmute_at <= ? AND notified=0", (now,)
            ) as cur:
                rows = await cur.fetchall()
            if rows:
                await db.execute(
                    "DELETE FROM mute_tracking WHERE unmute_at <= ? AND notified=0", (now,))
                await db.commit()
        for uid, gid in rows:
            try:
                user = await self.bot.fetch_user(uid)
                guild = self.bot.get_guild(gid)
                await try_dm(user, dm_embed(
                    "🔊 Your mute has expired",
                    0x57F287,
                    [("Server", guild.name if guild else f"Guild {gid}"),
                     ("Status", "You can now send messages again.")]
                ))
            except Exception:
                pass

    # ── Warn threshold logic ──────────────────────────────────
    async def check_warn_thresholds(self, guild: discord.Guild,
                                    member: discord.Member, count: int):
        kick_t = await get_setting(guild.id, 'warn_kick_threshold') or 3
        ban_t  = await get_setting(guild.id, 'warn_ban_threshold')  or 0
        mute_t = await get_setting(guild.id, 'warn_mute_threshold') or 0
        mute_m = await get_setting(guild.id, 'warn_mute_minutes')   or 10

        if ban_t and count >= ban_t:
            await try_dm(member, dm_embed("🔨 Auto-banned", 0xCC0000,
                [("Reason", f"Reached {ban_t} warnings")]))
            await member.ban(reason=f"Auto-ban: {ban_t}+ warns")
            await log_action(self.bot, f"Auto-ban ({ban_t} warns)", member,
                             self.bot.user, guild_id=guild.id)
            return "ban"
        if kick_t and count >= kick_t:
            await try_dm(member, dm_embed("👢 Auto-kicked", 0xFF4444,
                [("Reason", f"Reached {kick_t} warnings")]))
            await member.kick(reason=f"Auto-kick: {kick_t}+ warns")
            await log_action(self.bot, f"Auto-kick ({kick_t} warns)", member,
                             self.bot.user, guild_id=guild.id)
            return "kick"
        if mute_t and count >= mute_t:
            until = datetime.now(timezone.utc) + timedelta(minutes=int(mute_m))
            await member.edit(timed_out_until=until,
                              reason=f"Auto-mute: {mute_t}+ warns")
            await try_dm(member, dm_embed("🔇 Auto-muted", 0xFF8800,
                [("Reason",   f"Reached {mute_t} warnings"),
                 ("Duration", f"{mute_m} minutes")]))
            await log_action(self.bot, f"Auto-mute ({mute_t} warns)", member,
                             self.bot.user, guild_id=guild.id)
            return "mute"
        return None

    # ── Core warn logic ───────────────────────────────────────
    async def do_warn(self, guild: discord.Guild, moderator, member: discord.Member,
                      reason: str, expires_at=None, proof_url: str = None):
        count = await add_warn(member.id, guild.id, moderator.id,
                               reason, expires_at, proof_url)
        await log_action(self.bot, "Warn", member, moderator,
                         reason, guild.id, proof_url)
        fields = [("Server", guild.name), ("Reason", reason or "None"),
                  ("Total Warns", str(count))]
        if expires_at:
            fields.append(("Expires", f"<t:{int(expires_at.timestamp())}:R>"))
        if proof_url:
            fields.append(("📎 Proof", f"[Jump to message]({proof_url})"))
        await try_dm(member, dm_embed("⚠️ You have been warned", 0xFFAA00, fields))
        action = await self.check_warn_thresholds(guild, member, count)
        return count, action

    # ── Slash: warn ───────────────────────────────────────────
    @app_commands.command(name="warn", description="Warn a member")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Member to warn", reason="Reason",
                           expires_in="Optional expiry e.g. 7d 24h 30m")
    async def slash_warn(self, i: discord.Interaction,
                         member: discord.Member, reason: str = None,
                         expires_in: str = None):
        delta      = parse_duration(expires_in) if expires_in else None
        expires_at = datetime.now(timezone.utc) + delta if delta else None
        count, action = await self.do_warn(i.guild, i.user, member, reason, expires_at)
        e = discord.Embed(title="⚠️ Member Warned", color=0xFFAA00,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Member",      value=member.mention)
        e.add_field(name="By",          value=i.user.mention)
        e.add_field(name="Reason",      value=reason or "None", inline=False)
        e.add_field(name="Total Warns", value=str(count))
        if expires_at:
            e.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>")
        await i.response.send_message(embed=e)
        if action:
            await i.followup.send(embed=discord.Embed(
                description=f"{'👢' if action=='kick' else '🔨' if action=='ban' else '🔇'}"
                            f" Auto-{action}ed {member.mention} (warn threshold reached).",
                color=0xFF4444))

    # ── Slash: unwarn ─────────────────────────────────────────
    @app_commands.command(name="unwarn", description="Remove a warn by ID")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unwarn(self, i: discord.Interaction,
                           member: discord.Member, warn_id: int):
        if not await remove_warn_by_id(warn_id, i.guild.id):
            await i.response.send_message("❌ Warn ID not found.", ephemeral=True)
            return
        await log_action(self.bot, "Remove Warn", member, i.user,
                         f"Warn #{warn_id}", i.guild.id)
        await try_dm(member, dm_embed("✅ Warn Removed", 0x57F287,
            [("Server", i.guild.name),
             ("Info", f"Warn #{warn_id} has been removed from your record.")]))
        await i.response.send_message(embed=discord.Embed(
            description=f"✅ Removed warn `#{warn_id}` from {member.mention}.",
            color=0x57F287))

    # ── Slash: clearwarns ─────────────────────────────────────
    @app_commands.command(name="clearwarns", description="Clear all warns for a member")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_clearwarns(self, i: discord.Interaction, member: discord.Member):
        count = await clear_all_warns(member.id, i.guild.id)
        await log_action(self.bot, "Clear Warns", member, i.user, guild_id=i.guild.id)
        await try_dm(member, dm_embed("🗑️ Warns Cleared", 0x57F287,
            [("Server", i.guild.name),
             ("Info", f"All {count} warn(s) removed from your record.")]))
        await i.response.send_message(embed=discord.Embed(
            description=f"🗑️ Cleared **{count}** warn(s) from {member.mention}.",
            color=0x57F287))

    # ── Slash: warns ──────────────────────────────────────────
    @app_commands.command(name="warns", description="Check warns for a member")
    async def slash_warns(self, i: discord.Interaction, member: discord.Member = None):
        target = member or i.user
        rows   = await get_all_warns(target.id, i.guild.id)
        e = discord.Embed(title=f"⚠️ Warns — {target.display_name}",
                          color=0xFFAA00, timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        if not rows:
            e.description = "No active warns ✅"
        else:
            for wid, mid, reason, proof_url, exp, ts in rows:
                mod    = i.guild.get_member(mid)
                by     = mod.mention if mod else f"<@{mid}>"
                expiry = (f"\nExpires: <t:{int(datetime.fromisoformat(exp).timestamp())}:R>"
                          if exp else "")
                proof  = (f"\n[📎 Proof]({proof_url})" if proof_url else "")
                e.add_field(
                    name=f"Warn #{wid} — {ts[:10]}",
                    value=f"By: {by}\nReason: {reason or 'None'}{expiry}{proof}",
                    inline=False)
        await i.response.send_message(embed=e)

    # ── Slash: history ────────────────────────────────────────
    @app_commands.command(name="history", description="View all mod actions taken against a user")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_history(self, i: discord.Interaction, member: discord.Member):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action,moderator_id,reason,proof_url,timestamp FROM mod_logs"
                " WHERE guild_id=? AND user_id=? ORDER BY timestamp DESC LIMIT 25",
                (i.guild.id, member.id)
            ) as cur:
                rows = await cur.fetchall()
        e = discord.Embed(title=f"📜 Mod History — {member.display_name}",
                          color=0xFF4444, timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=member.display_avatar.url)
        if not rows:
            e.description = "No mod actions on record."
        else:
            for action, mid, reason, proof_url, ts in rows:
                mod   = i.guild.get_member(mid)
                proof = f" | [📎]({proof_url})" if proof_url else ""
                e.add_field(
                    name=f"{action} — {ts[:10]}",
                    value=f"By: {mod.mention if mod else f'<@{mid}>'}\n"
                          f"Reason: {reason or 'None'}{proof}",
                    inline=False)
        await i.response.send_message(embed=e)

    # ── Slash: modlogs ────────────────────────────────────────
    @app_commands.command(name="modlogs", description="Show mod actions by a moderator")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_modlogs(self, i: discord.Interaction, moderator: discord.Member):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action,user_id,reason,timestamp FROM mod_logs"
                " WHERE guild_id=? AND moderator_id=? ORDER BY timestamp DESC LIMIT 20",
                (i.guild.id, moderator.id)
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
        await i.response.send_message(embed=e)

    # ── Slash: mute ───────────────────────────────────────────
    @app_commands.command(name="mute", description="Timeout a member")
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Member", minutes="Duration in minutes", reason="Reason")
    async def slash_mute(self, i: discord.Interaction,
                         member: discord.Member, minutes: int, reason: str = None):
        await i.response.defer()
        member = await fetch_member(self.bot, i.guild_id, member.id)
        until  = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await member.edit(timed_out_until=until, reason=reason)
        await log_action(self.bot, f"Mute {minutes}min", member, i.user, reason, i.guild_id)
        # Track for unmute DM
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO mute_tracking (user_id,guild_id,unmute_at,notified)"
                " VALUES (?,?,?,0)",
                (member.id, i.guild_id, until.isoformat()))
            await db.commit()
        e = discord.Embed(title="🔇 Member Muted", color=0xFF8800,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="Member",   value=member.mention)
        e.add_field(name="By",       value=i.user.mention)
        e.add_field(name="Duration", value=f"{minutes} min")
        e.add_field(name="Reason",   value=reason or "None", inline=False)
        e.add_field(name="Unmuted",  value=f"<t:{int(until.timestamp())}:R>")
        await i.followup.send(embed=e)
        await try_dm(member, dm_embed("🔇 You have been muted", 0xFF8800,
            [("Server",   i.guild.name),
             ("Duration", f"{minutes} min"),
             ("Unmuted",  f"<t:{int(until.timestamp())}:R>"),
             ("Reason",   reason or "None")]))

    # ── Slash: unmute ─────────────────────────────────────────
    @app_commands.command(name="unmute", description="Remove a member's timeout")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unmute(self, i: discord.Interaction, member: discord.Member):
        await i.response.defer()
        member = await fetch_member(self.bot, i.guild_id, member.id)
        await member.edit(timed_out_until=None)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM mute_tracking WHERE user_id=? AND guild_id=?",
                (member.id, i.guild_id))
            await db.commit()
        await log_action(self.bot, "Unmute", member, i.user, guild_id=i.guild_id)
        await i.followup.send(embed=discord.Embed(
            description=f"🔊 Unmuted {member.mention}.", color=0x57F287))
        await try_dm(member, dm_embed("🔊 You have been unmuted", 0x57F287,
            [("Server", i.guild.name), ("By", str(i.user))]))

    # ── Slash: kick ───────────────────────────────────────────
    @app_commands.command(name="kick", description="Kick a member")
    @app_commands.default_permissions(kick_members=True)
    async def slash_kick(self, i: discord.Interaction,
                         member: discord.Member, reason: str = None):
        await try_dm(member, dm_embed("👢 You have been kicked", 0xFF4444,
            [("Server", i.guild.name), ("Reason", reason or "None")]))
        await member.kick(reason=reason)
        await log_action(self.bot, "Kick", member, i.user, reason, i.guild.id)
        e = discord.Embed(title="👢 Member Kicked", color=0xFF4444,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="By",     value=i.user.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await i.response.send_message(embed=e)

    # ── Slash: ban ────────────────────────────────────────────
    @app_commands.command(name="ban", description="Ban a member")
    @app_commands.default_permissions(ban_members=True)
    async def slash_ban(self, i: discord.Interaction,
                        member: discord.Member, reason: str = None):
        await try_dm(member, dm_embed("🔨 You have been banned", 0xCC0000,
            [("Server", i.guild.name), ("Reason", reason or "None")]))
        await member.ban(reason=reason)
        await log_action(self.bot, "Ban", member, i.user, reason, i.guild.id)
        e = discord.Embed(title="🔨 Member Banned", color=0xCC0000,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="By",     value=i.user.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await i.response.send_message(embed=e)

    # ── Slash: tempban ────────────────────────────────────────
    @app_commands.command(name="tempban", description="Temporarily ban a member")
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(duration="e.g. 1d 12h 30m")
    async def slash_tempban(self, i: discord.Interaction,
                            member: discord.Member, duration: str, reason: str = None):
        delta = parse_duration(duration)
        if not delta:
            await i.response.send_message("❌ Invalid duration.", ephemeral=True)
            return
        unban_at = datetime.now(timezone.utc) + delta
        await try_dm(member, dm_embed("🔨 Temporarily Banned", 0xCC0000,
            [("Server", i.guild.name), ("Duration", duration),
             ("Unban At", f"<t:{int(unban_at.timestamp())}:R>"),
             ("Reason", reason or "None")]))
        await member.ban(reason=reason)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO tempbans (user_id,guild_id,unban_at) VALUES (?,?,?)",
                (member.id, i.guild.id, unban_at.isoformat()))
            await db.commit()
        await log_action(self.bot, f"Tempban ({duration})", member, i.user, reason, i.guild.id)
        e = discord.Embed(title="🔨 Member Tempbanned", color=0xCC0000,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member",   value=member.mention)
        e.add_field(name="Duration", value=duration)
        e.add_field(name="Unban",    value=f"<t:{int(unban_at.timestamp())}:R>")
        e.add_field(name="Reason",   value=reason or "None", inline=False)
        await i.response.send_message(embed=e)

    # ── Slash: jail / unjail ──────────────────────────────────
    @app_commands.command(name="jail", description="Jail a member")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_jail(self, i: discord.Interaction,
                         member: discord.Member, reason: str = None):
        await i.response.defer()
        await self._do_jail(i.guild, i.user, member, reason)
        e = discord.Embed(title="🔒 Member Jailed", color=0xFF6600,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Member", value=member.mention)
        e.add_field(name="By",     value=i.user.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await i.followup.send(embed=e)

    @app_commands.command(name="unjail", description="Release a member from jail")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_unjail(self, i: discord.Interaction, member: discord.Member):
        await i.response.defer()
        await self._do_unjail(i.guild, i.user, member)
        await i.followup.send(embed=discord.Embed(
            description=f"🔓 Released {member.mention}.", color=0x57F287))

    # ── Slash: purge ──────────────────────────────────────────
    @app_commands.command(name="purge", description="Delete messages (max 100)")
    @app_commands.default_permissions(manage_messages=True)
    async def slash_purge(self, i: discord.Interaction, amount: int):
        if not 1 <= amount <= 100:
            await i.response.send_message("❌ 1–100 only.", ephemeral=True); return
        await i.response.defer(ephemeral=True)
        deleted = await i.channel.purge(limit=amount)
        await log_action(self.bot, f"Purge ({len(deleted)} msgs)",
                         i.user, i.user, guild_id=i.guild_id)
        await i.followup.send(f"🗑️ Deleted **{len(deleted)}** messages.", ephemeral=True)

    # ── Slash: nick / slowmode ────────────────────────────────
    @app_commands.command(name="nick", description="Change a member's nickname")
    @app_commands.default_permissions(manage_nicknames=True)
    async def slash_nick(self, i: discord.Interaction,
                         member: discord.Member, nickname: str = None):
        old = member.display_name
        await member.edit(nick=nickname)
        await log_action(self.bot, "Nick Change", member, i.user,
                         f"{old} → {nickname or 'reset'}", i.guild.id)
        await i.response.send_message(embed=discord.Embed(
            description=f"✅ {member.mention} → **{nickname or 'reset'}**",
            color=0x57F287))

    @app_commands.command(name="slowmode", description="Set channel slowmode (0 = off)")
    @app_commands.default_permissions(manage_channels=True)
    async def slash_slowmode(self, i: discord.Interaction, seconds: int):
        await i.channel.edit(slowmode_delay=seconds)
        await i.response.send_message(embed=discord.Embed(
            description=f"⏱️ Slowmode → **{seconds}s**" if seconds else "⏱️ Slowmode disabled.",
            color=0x57F287))

    # ── Slash: lookup ─────────────────────────────────────────
    @app_commands.command(name="lookup", description="Fetch info about any user by ID")
    @app_commands.default_permissions(moderate_members=True)
    async def slash_lookup(self, i: discord.Interaction, user_id: str):
        await i.response.defer()
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.NotFound):
            await i.followup.send("❌ User not found.", ephemeral=True); return
        warns = await get_warn_count(user.id, i.guild.id)
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM mod_logs WHERE guild_id=? AND user_id=?",
                (i.guild.id, user.id)
            ) as cur:
                log_count = (await cur.fetchone())[0]
        member = i.guild.get_member(user.id)
        e = discord.Embed(title=str(user), color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=user.display_avatar.url)
        e.add_field(name="ID",         value=str(user.id))
        e.add_field(name="Created",    value=f"<t:{int(user.created_at.timestamp())}:R>")
        e.add_field(name="In Server",  value="✅ Yes" if member else "❌ No")
        e.add_field(name="Active Warns",   value=str(warns))
        e.add_field(name="Total Mod Logs", value=str(log_count))
        if member:
            e.add_field(name="Joined", value=f"<t:{int(member.joined_at.timestamp())}:R>")
        await i.followup.send(embed=e)

    # ─────────────────────────────────────────────────────────
    #  PREFIX COMMANDS  (reply-to-target support)
    # ─────────────────────────────────────────────────────────

    async def _resolve_target(self, ctx: commands.Context,
                               member: discord.Member = None) -> discord.Member:
        """Return member arg, or fall back to replied-to message author."""
        if member:
            return member
        reply_target = await get_reply_target(ctx)
        if reply_target:
            return reply_target
        return None

    def _proof_embed_field(self, embed: discord.Embed,
                            proof: tuple | None) -> discord.Embed:
        if proof:
            url, preview = proof
            embed.add_field(name="📎 Proof",
                            value=f"[Jump to message]({url})\n> {preview}",
                            inline=False)
        return embed

    @commands.command(name="warn")
    @commands.has_permissions(moderate_members=True)
    async def prefix_warn(self, ctx: commands.Context,
                          member: discord.Member = None, *, args: str = ""):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        tokens     = args.split()
        expires_at = None
        if tokens:
            d = parse_duration(tokens[-1])
            if d:
                expires_at = datetime.now(timezone.utc) + d
                tokens     = tokens[:-1]
        reason = " ".join(tokens) or None
        proof  = await get_proof(ctx)
        count, action = await self.do_warn(
            ctx.guild, ctx.author, target, reason, expires_at,
            proof[0] if proof else None)
        e = discord.Embed(title="⚠️ Member Warned", color=0xFFAA00,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Member",      value=target.mention)
        e.add_field(name="By",          value=ctx.author.mention)
        e.add_field(name="Reason",      value=reason or "None", inline=False)
        e.add_field(name="Total Warns", value=str(count))
        if expires_at:
            e.add_field(name="Expires", value=f"<t:{int(expires_at.timestamp())}:R>")
        self._proof_embed_field(e, proof)
        await ctx.send(embed=e)
        if action:
            await ctx.send(embed=discord.Embed(
                description=f"{'👢' if action=='kick' else '🔨' if action=='ban' else '🔇'}"
                            f" Auto-{action}ed {target.mention} (threshold reached).",
                color=0xFF4444))

    @commands.command(name="unwarn")
    @commands.has_permissions(moderate_members=True)
    async def prefix_unwarn(self, ctx: commands.Context,
                            member: discord.Member, warn_id: int):
        if not await remove_warn_by_id(warn_id, ctx.guild.id):
            await ctx.reply("❌ Warn ID not found."); return
        await log_action(self.bot, "Remove Warn", member, ctx.author,
                         f"Warn #{warn_id}", ctx.guild.id)
        await try_dm(member, dm_embed("✅ Warn Removed", 0x57F287,
            [("Server", ctx.guild.name),
             ("Info", f"Warn #{warn_id} removed from your record.")]))
        await ctx.send(embed=discord.Embed(
            description=f"✅ Removed warn `#{warn_id}` from {member.mention}.",
            color=0x57F287))

    @commands.command(name="clearwarns")
    @commands.has_permissions(moderate_members=True)
    async def prefix_clearwarns(self, ctx: commands.Context,
                                member: discord.Member = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        count = await clear_all_warns(target.id, ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"🗑️ Cleared **{count}** warn(s) from {target.mention}.",
            color=0x57F287))

    @commands.command(name="warns")
    async def prefix_warns(self, ctx: commands.Context, member: discord.Member = None):
        target = await self._resolve_target(ctx, member) or ctx.author
        rows   = await get_all_warns(target.id, ctx.guild.id)
        e = discord.Embed(title=f"⚠️ Warns — {target.display_name}", color=0xFFAA00)
        if not rows:
            e.description = "No active warns ✅"
        else:
            for wid, mid, reason, proof_url, exp, ts in rows:
                proof = f" | [📎]({proof_url})" if proof_url else ""
                e.add_field(name=f"Warn #{wid} — {ts[:10]}",
                            value=f"Reason: {reason or 'None'}{proof}", inline=False)
        await ctx.send(embed=e)

    @commands.command(name="history")
    @commands.has_permissions(moderate_members=True)
    async def prefix_history(self, ctx: commands.Context,
                             member: discord.Member = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action,moderator_id,reason,proof_url,timestamp FROM mod_logs"
                " WHERE guild_id=? AND user_id=? ORDER BY timestamp DESC LIMIT 25",
                (ctx.guild.id, target.id)
            ) as cur:
                rows = await cur.fetchall()
        e = discord.Embed(title=f"📜 History — {target.display_name}", color=0xFF4444)
        if not rows:
            e.description = "No mod actions on record."
        else:
            for action, mid, reason, proof_url, ts in rows:
                mod   = ctx.guild.get_member(mid)
                proof = f" | [📎]({proof_url})" if proof_url else ""
                e.add_field(name=f"{action} — {ts[:10]}",
                            value=f"By: {mod.mention if mod else f'<@{mid}>'}\n"
                                  f"Reason: {reason or 'None'}{proof}",
                            inline=False)
        await ctx.send(embed=e)

    @commands.command(name="modlogs")
    @commands.has_permissions(moderate_members=True)
    async def prefix_modlogs(self, ctx: commands.Context, moderator: discord.Member):
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action,user_id,reason,timestamp FROM mod_logs"
                " WHERE guild_id=? AND moderator_id=? ORDER BY timestamp DESC LIMIT 20",
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
    async def prefix_mute(self, ctx: commands.Context,
                          member: discord.Member = None,
                          minutes: int = None, *, reason: str = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        if minutes is None:
            await ctx.reply("❌ Specify duration in minutes e.g. `?mute @user 10`"); return
        proof = await get_proof(ctx)
        target = await fetch_member(self.bot, ctx.guild.id, target.id)
        until  = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        await target.edit(timed_out_until=until, reason=reason)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO mute_tracking (user_id,guild_id,unmute_at,notified)"
                " VALUES (?,?,?,0)",
                (target.id, ctx.guild.id, until.isoformat()))
            await db.commit()
        await log_action(self.bot, f"Mute {minutes}min", target, ctx.author,
                         reason, ctx.guild.id, proof[0] if proof else None)
        e = discord.Embed(title="🔇 Member Muted", color=0xFF8800)
        e.set_thumbnail(url=target.display_avatar.url)
        e.add_field(name="Member",   value=target.mention)
        e.add_field(name="Duration", value=f"{minutes} min")
        e.add_field(name="Reason",   value=reason or "None", inline=False)
        self._proof_embed_field(e, proof)
        await ctx.send(embed=e)
        await try_dm(target, dm_embed("🔇 You have been muted", 0xFF8800,
            [("Server", ctx.guild.name), ("Duration", f"{minutes} min"),
             ("Reason",  reason or "None"),
             ("Unmuted", f"<t:{int(until.timestamp())}:R>")]))

    @commands.command(name="unmute")
    @commands.has_permissions(moderate_members=True)
    async def prefix_unmute(self, ctx: commands.Context, member: discord.Member = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        target = await fetch_member(self.bot, ctx.guild.id, target.id)
        await target.edit(timed_out_until=None)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "DELETE FROM mute_tracking WHERE user_id=? AND guild_id=?",
                (target.id, ctx.guild.id))
            await db.commit()
        await log_action(self.bot, "Unmute", target, ctx.author, guild_id=ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"🔊 Unmuted {target.mention}.", color=0x57F287))
        await try_dm(target, dm_embed("🔊 You have been unmuted", 0x57F287,
            [("Server", ctx.guild.name), ("By", str(ctx.author))]))

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    async def prefix_kick(self, ctx: commands.Context,
                          member: discord.Member = None, *, reason: str = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        proof = await get_proof(ctx)
        await try_dm(target, dm_embed("👢 You have been kicked", 0xFF4444,
            [("Server", ctx.guild.name), ("Reason", reason or "None")]))
        await target.kick(reason=reason)
        await log_action(self.bot, "Kick", target, ctx.author,
                         reason, ctx.guild.id, proof[0] if proof else None)
        e = discord.Embed(title="👢 Member Kicked", color=0xFF4444)
        e.add_field(name="Member", value=target.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        self._proof_embed_field(e, proof)
        await ctx.send(embed=e)

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    async def prefix_ban(self, ctx: commands.Context,
                         member: discord.Member = None, *, reason: str = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        proof = await get_proof(ctx)
        await try_dm(target, dm_embed("🔨 You have been banned", 0xCC0000,
            [("Server", ctx.guild.name), ("Reason", reason or "None")]))
        await target.ban(reason=reason)
        await log_action(self.bot, "Ban", target, ctx.author,
                         reason, ctx.guild.id, proof[0] if proof else None)
        e = discord.Embed(title="🔨 Member Banned", color=0xCC0000)
        e.add_field(name="Member", value=target.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        self._proof_embed_field(e, proof)
        await ctx.send(embed=e)

    @commands.command(name="tempban")
    @commands.has_permissions(ban_members=True)
    async def prefix_tempban(self, ctx: commands.Context,
                             member: discord.Member = None,
                             duration: str = None, *, reason: str = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        if not duration:
            await ctx.reply("❌ Specify duration e.g. `?tempban @user 7d`"); return
        delta = parse_duration(duration)
        if not delta:
            await ctx.reply("❌ Invalid duration. Use e.g. `1d`, `12h`, `30m`."); return
        proof    = await get_proof(ctx)
        unban_at = datetime.now(timezone.utc) + delta
        await try_dm(target, dm_embed("🔨 Temporarily Banned", 0xCC0000,
            [("Server", ctx.guild.name), ("Duration", duration),
             ("Unban", f"<t:{int(unban_at.timestamp())}:R>"),
             ("Reason", reason or "None")]))
        await target.ban(reason=reason)
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO tempbans (user_id,guild_id,unban_at) VALUES (?,?,?)",
                (target.id, ctx.guild.id, unban_at.isoformat()))
            await db.commit()
        await log_action(self.bot, f"Tempban ({duration})", target, ctx.author,
                         reason, ctx.guild.id, proof[0] if proof else None)
        e = discord.Embed(title="🔨 Tempbanned", color=0xCC0000)
        e.add_field(name="Member",   value=target.mention)
        e.add_field(name="Duration", value=duration)
        e.add_field(name="Unban",    value=f"<t:{int(unban_at.timestamp())}:R>")
        self._proof_embed_field(e, proof)
        await ctx.send(embed=e)

    @commands.command(name="jail")
    @commands.has_permissions(moderate_members=True)
    async def prefix_jail(self, ctx: commands.Context,
                          member: discord.Member = None, *, reason: str = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        await self._do_jail(ctx.guild, ctx.author, target, reason)
        e = discord.Embed(title="🔒 Member Jailed", color=0xFF6600)
        e.add_field(name="Member", value=target.mention)
        e.add_field(name="Reason", value=reason or "None", inline=False)
        await ctx.send(embed=e)

    @commands.command(name="unjail")
    @commands.has_permissions(moderate_members=True)
    async def prefix_unjail(self, ctx: commands.Context, member: discord.Member = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member or reply to their message."); return
        await self._do_unjail(ctx.guild, ctx.author, target)
        await ctx.send(embed=discord.Embed(
            description=f"🔓 Released {target.mention}.", color=0x57F287))

    @commands.command(name="purge")
    @commands.has_permissions(manage_messages=True)
    async def prefix_purge(self, ctx: commands.Context, amount: int):
        if not 1 <= amount <= 100:
            await ctx.reply("❌ Between 1–100."); return
        await ctx.message.delete()
        deleted = await ctx.channel.purge(limit=amount)
        m = await ctx.send(f"🗑️ Deleted **{len(deleted)}** messages.")
        await asyncio.sleep(5)
        try:
            await m.delete()
        except discord.NotFound:
            pass

    @commands.command(name="nick")
    @commands.has_permissions(manage_nicknames=True)
    async def prefix_nick(self, ctx: commands.Context,
                          member: discord.Member = None, *, nickname: str = None):
        target = await self._resolve_target(ctx, member)
        if not target:
            await ctx.reply("❌ Specify a member."); return
        old = target.display_name
        await target.edit(nick=nickname)
        await log_action(self.bot, "Nick Change", target, ctx.author,
                         f"{old} → {nickname or 'reset'}", ctx.guild.id)
        await ctx.send(embed=discord.Embed(
            description=f"✅ {target.mention} → **{nickname or 'reset'}**",
            color=0x57F287))

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    async def prefix_slowmode(self, ctx: commands.Context, seconds: int):
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(embed=discord.Embed(
            description=f"⏱️ Slowmode → **{seconds}s**" if seconds else "⏱️ Slowmode disabled.",
            color=0x57F287))

    @commands.command(name="lookup")
    @commands.has_permissions(moderate_members=True)
    async def prefix_lookup(self, ctx: commands.Context, user_id: str):
        try:
            user = await self.bot.fetch_user(int(user_id))
        except (ValueError, discord.NotFound):
            await ctx.reply("❌ User not found."); return
        warns = await get_warn_count(user.id, ctx.guild.id)
        member = ctx.guild.get_member(user.id)
        e = discord.Embed(title=str(user), color=0x5865F2)
        e.set_thumbnail(url=user.display_avatar.url)
        e.add_field(name="ID",        value=str(user.id))
        e.add_field(name="Created",   value=f"<t:{int(user.created_at.timestamp())}:R>")
        e.add_field(name="In Server", value="✅" if member else "❌")
        e.add_field(name="Warns",     value=str(warns))
        await ctx.send(embed=e)

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
            await db.execute("DELETE FROM jailed_roles WHERE user_id=? AND guild_id=?",
                             (member.id, guild.id))
            for rid in role_ids:
                await db.execute(
                    "INSERT INTO jailed_roles (user_id,guild_id,role_id) VALUES (?,?,?)",
                    (member.id, guild.id, rid))
            await db.commit()
        roles_to_remove = [r for r in member.roles if not r.is_default()]
        if roles_to_remove:
            await member.remove_roles(*roles_to_remove, reason="Jailed")
        await member.add_roles(jail_role, reason=reason or "Jailed")
        await log_action(self.bot, "Jail", member, moderator, reason, guild.id)
        await try_dm(member, dm_embed("🔒 You have been jailed", 0xFF6600,
            [("Server", guild.name), ("Reason", reason or "None")]))

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
            await db.execute("DELETE FROM jailed_roles WHERE user_id=? AND guild_id=?",
                             (member.id, guild.id))
            await db.commit()
        roles_to_add = [guild.get_role(r[0]) for r in rows if guild.get_role(r[0])]
        if roles_to_add:
            await member.add_roles(*roles_to_add, reason="Unjailed")
        await log_action(self.bot, "Unjail", member, moderator, guild_id=guild.id)
        await try_dm(member, dm_embed("🔓 You have been released from jail", 0x57F287,
            [("Server", guild.name)]))

async def setup(bot):
    await bot.add_cog(Moderation(bot))
