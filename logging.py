# cogs/logging.py — event listeners, snipe, starboard, anti-raid,
#                   custom command runner, welcome, autorole

import asyncio
from datetime import datetime, timezone

import discord
from discord.ext import commands
import aiosqlite

from cogs.utils import DB, get_setting, set_setting, snipe_cache

class Logging(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def send_log(self, guild_id: int, category: str, embed: discord.Embed):
        """category: log_mod_id | log_message_id | log_member_id | log_server_id"""
        ch_id = await get_setting(guild_id, category)
        if not ch_id:
            return
        ch = self.bot.get_channel(ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

    # ── Snipe cache ───────────────────────────────────────────
    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        if message.author.bot:
            return
        cache = snipe_cache[message.channel.id]
        cache.insert(0, {
            "content": message.content or "*[no text content]*",
            "author":  str(message.author),
            "avatar":  message.author.display_avatar.url,
            "time":    datetime.now(timezone.utc)
        })
        snipe_cache[message.channel.id] = cache[:3]

        # Message delete log
        if message.guild:
            e = discord.Embed(title="🗑️ Message Deleted", color=0xFF4444,
                              timestamp=datetime.now(timezone.utc))
            e.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
            e.add_field(name="Channel", value=message.channel.mention)
            e.add_field(name="Content", value=message.content[:1024] or "*empty*", inline=False)
            await self.send_log(message.guild.id, 'log_message_id', e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild or before.content == after.content:
            return
        e = discord.Embed(title="✏️ Message Edited", color=0xFFAA00,
                          timestamp=datetime.now(timezone.utc))
        e.set_author(name=str(before.author), icon_url=before.author.display_avatar.url)
        e.add_field(name="Channel", value=before.channel.mention)
        e.add_field(name="Before",  value=before.content[:512] or "*empty*", inline=False)
        e.add_field(name="After",   value=after.content[:512]  or "*empty*", inline=False)
        e.add_field(name="Jump",    value=f"[View]({after.jump_url})")
        await self.send_log(before.guild.id, 'log_message_id', e)

    # ── Member join / leave ───────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gid = member.guild.id

        # Anti-raid check
        enabled = await get_setting(gid, 'antiraid_enabled')
        if enabled:
            threshold = await get_setting(gid, 'antiraid_threshold') or 10
            seconds   = await get_setting(gid, 'antiraid_seconds')   or 10
            action    = await get_setting(gid, 'antiraid_action')     or 'slowmode'
            tracker   = self.bot.join_tracker
            now       = datetime.now(timezone.utc).timestamp()
            tracker.setdefault(gid, [])
            tracker[gid] = [t for t in tracker[gid] if now - t < seconds]
            tracker[gid].append(now)
            if len(tracker[gid]) >= threshold:
                await self._trigger_antiraid(member.guild, action)

        # Autorole
        role_id = await get_setting(gid, 'autorole_id')
        if role_id:
            role = member.guild.get_role(role_id)
            if role:
                try:
                    await member.add_roles(role, reason="Autorole")
                except discord.Forbidden:
                    pass

        # Welcome message
        wc_id = await get_setting(gid, 'welcome_channel_id')
        w_msg = await get_setting(gid, 'welcome_message')
        if wc_id and w_msg:
            ch = self.bot.get_channel(wc_id)
            if ch:
                text = (w_msg
                        .replace("{user}",   member.mention)
                        .replace("{name}",   member.display_name)
                        .replace("{server}", member.guild.name)
                        .replace("{count}",  str(member.guild.member_count)))
                e = discord.Embed(description=text, color=0x57F287,
                                  timestamp=datetime.now(timezone.utc))
                e.set_thumbnail(url=member.display_avatar.url)
                e.set_footer(text=f"Member #{member.guild.member_count}")
                await ch.send(embed=e)

        # Member join log
        e = discord.Embed(title="📥 Member Joined", color=0x57F287,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="User",       value=f"{member} ({member.mention})")
        e.add_field(name="Account Age",value=f"<t:{int(member.created_at.timestamp())}:R>")
        e.add_field(name="Members",    value=str(member.guild.member_count))
        await self.send_log(gid, 'log_member_id', e)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        e = discord.Embed(title="📤 Member Left", color=0xFF4444,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="User",    value=str(member))
        e.add_field(name="Members", value=str(member.guild.member_count))
        roles = [r.mention for r in member.roles[1:]]
        if roles:
            e.add_field(name="Roles", value=" ".join(roles[:10]), inline=False)
        await self.send_log(member.guild.id, 'log_member_id', e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        gid = before.guild.id
        # Nickname change
        if before.nick != after.nick:
            e = discord.Embed(title="📝 Nickname Changed", color=0x5865F2,
                              timestamp=datetime.now(timezone.utc))
            e.set_thumbnail(url=after.display_avatar.url)
            e.add_field(name="User",   value=after.mention)
            e.add_field(name="Before", value=before.nick or "*none*")
            e.add_field(name="After",  value=after.nick  or "*none*")
            await self.send_log(gid, 'log_member_id', e)
        # Role change
        added   = set(after.roles)  - set(before.roles)
        removed = set(before.roles) - set(after.roles)
        if added or removed:
            e = discord.Embed(title="🔄 Roles Updated", color=0xFFAA00,
                              timestamp=datetime.now(timezone.utc))
            e.set_thumbnail(url=after.display_avatar.url)
            e.add_field(name="User", value=after.mention)
            if added:
                e.add_field(name="Added",   value=" ".join(r.mention for r in added))
            if removed:
                e.add_field(name="Removed", value=" ".join(r.mention for r in removed))
            await self.send_log(gid, 'log_member_id', e)

    # ── Voice ─────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after:  discord.VoiceState):
        if before.channel == after.channel:
            return
        if before.channel is None:
            desc  = f"📞 **{member}** joined **{after.channel.name}**"
            color = 0x57F287
        elif after.channel is None:
            desc  = f"📴 **{member}** left **{before.channel.name}**"
            color = 0xFF4444
        else:
            desc  = f"🔀 **{member}** moved **{before.channel.name}** → **{after.channel.name}**"
            color = 0xFFAA00
        e = discord.Embed(description=desc, color=color, timestamp=datetime.now(timezone.utc))
        await self.send_log(member.guild.id, 'log_server_id', e)

    # ── Server events ─────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        e = discord.Embed(title="✅ Channel Created", color=0x57F287,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Name", value=channel.mention)
        e.add_field(name="Type", value=str(channel.type))
        await self.send_log(channel.guild.id, 'log_server_id', e)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        e = discord.Embed(title="🗑️ Channel Deleted", color=0xFF4444,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Name", value=f"#{channel.name}")
        e.add_field(name="Type", value=str(channel.type))
        await self.send_log(channel.guild.id, 'log_server_id', e)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        e = discord.Embed(title="🔗 Invite Created", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="By",      value=invite.inviter.mention if invite.inviter else "Unknown")
        e.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown")
        e.add_field(name="Code",    value=invite.code)
        e.add_field(name="Expires", value=(
            f"<t:{int(invite.expires_at.timestamp())}:R>" if invite.expires_at else "Never"))
        await self.send_log(invite.guild.id, 'log_server_id', e)

    # ── Starboard ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return
        gid      = payload.guild_id
        emoji    = await get_setting(gid, 'starboard_emoji') or '⭐'
        if str(payload.emoji) != emoji:
            return
        threshold = int(await get_setting(gid, 'starboard_threshold') or 3)
        sb_ch_id  = await get_setting(gid, 'starboard_channel_id')
        if not sb_ch_id:
            return

        # Check if already posted
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT 1 FROM starboard_posted WHERE guild_id=? AND message_id=?",
                (gid, payload.message_id)
            ) as cur:
                if await cur.fetchone():
                    return

        channel = self.bot.get_channel(payload.channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
        except Exception:
            return

        # Count reactions
        reaction_count = 0
        for r in message.reactions:
            if str(r.emoji) == emoji:
                reaction_count = r.count
                break
        if reaction_count < threshold:
            return

        # Mark as posted
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR IGNORE INTO starboard_posted (guild_id, message_id) VALUES (?,?)",
                (gid, payload.message_id))
            await db.commit()

        sb_channel = self.bot.get_channel(sb_ch_id)
        if not sb_channel:
            return

        # Build embed
        e = discord.Embed(color=0xFFD700, timestamp=message.created_at)
        e.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        if message.content:
            e.description = message.content[:2048]
        if message.attachments:
            e.set_image(url=message.attachments[0].url)
        e.add_field(name="Source", value=f"[Jump to message]({message.jump_url})")

        # If message is a reply, include parent
        parent_embed = None
        if message.reference:
            try:
                parent = await channel.fetch_message(message.reference.message_id)
                parent_embed = discord.Embed(
                    color=0xB8860B,
                    description=f"**Replying to {parent.author.mention}:**\n{parent.content[:512]}")
                parent_embed.set_author(name=str(parent.author),
                                        icon_url=parent.author.display_avatar.url)
            except Exception:
                pass

        content = f"{emoji} **{reaction_count}** | {message.author.mention}"
        embeds  = [e]
        if parent_embed:
            embeds = [parent_embed, e]

        await sb_channel.send(content=content, embeds=embeds)

    # ── Anti-raid action ──────────────────────────────────────
    async def _trigger_antiraid(self, guild: discord.Guild, action: str):
        log_ch_id = await get_setting(guild.id, 'log_server_id') or await get_setting(guild.id, 'log_mod_id')
        if log_ch_id:
            alert = self.bot.get_channel(log_ch_id)
            if alert:
                e = discord.Embed(title="🚨 Anti-Raid Triggered!", color=0xFF0000,
                                  timestamp=datetime.now(timezone.utc))
                e.add_field(name="Action", value=action)
                await alert.send(embed=e)

        if action == 'slowmode':
            for ch in guild.text_channels:
                try:
                    await ch.edit(slowmode_delay=60)
                except Exception:
                    pass
        elif action == 'lockdown':
            for ch in guild.text_channels:
                try:
                    overwrite = ch.overwrites_for(guild.default_role)
                    overwrite.send_messages = False
                    await ch.set_permissions(guild.default_role, overwrite=overwrite)
                except Exception:
                    pass
        elif action == 'kick_new':
            now = datetime.now(timezone.utc)
            for member in guild.members:
                if (now - member.joined_at).total_seconds() < 30:
                    try:
                        await member.kick(reason="Anti-raid: new member during raid")
                    except Exception:
                        pass

    # ── Custom commands prefix runner ─────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        prefix = self.bot.prefix_cache.get(message.guild.id)
        if prefix is None:
            async with aiosqlite.connect(DB) as db:
                async with db.execute(
                    "SELECT prefix FROM guild_settings WHERE guild_id=?",
                    (message.guild.id,)
                ) as cur:
                    row = await cur.fetchone()
            prefix = row[0] if (row and row[0]) else "?"
            self.bot.prefix_cache[message.guild.id] = prefix

        if not message.content.startswith(prefix):
            return

        invoked = message.content[len(prefix):].split()[0].lower()

        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action_type, value FROM custom_commands WHERE guild_id=? AND name=?",
                (message.guild.id, invoked)
            ) as cur:
                row = await cur.fetchone()
        if not row:
            return

        action_type, value = row

        if action_type == 'message':
            await message.channel.send(value)

        elif action_type == 'ping':
            await message.channel.send(
                value, allowed_mentions=discord.AllowedMentions.all())

        elif action_type == 'alias':
            # Rewrite message content to run the aliased command
            rest = message.content[len(prefix) + len(invoked):]
            fake_content = f"{prefix}{value}{rest}"
            message = message  # can't mutate; process via ctx manually
            ctx = await self.bot.get_context(message)
            ctx.message.content = fake_content
            new_ctx = await self.bot.get_context(
                discord.Message.__new__(discord.Message)
            )
            # Simpler: just send it as a new invocation
            try:
                parts   = value.strip().split()
                cmd     = self.bot.get_command(parts[0])
                if cmd:
                    rest_args = message.content[len(prefix)+len(invoked):].strip()
                    alias_msg = await message.channel.send(f"{prefix}{value} {rest_args}")
                    new_ctx   = await self.bot.get_context(alias_msg)
                    if new_ctx.valid:
                        await self.bot.invoke(new_ctx)
                    await alias_msg.delete()
            except Exception as ex:
                print(f"Alias error: {ex}")

async def setup(bot):
    await bot.add_cog(Logging(bot))
