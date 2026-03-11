# cogs/events.py — SINGLE on_message hub + all event listeners
# This is the only cog with on_message. All other cogs expose a process()
# method that this cog calls. Eliminates all listener conflicts.
import asyncio
from datetime import datetime, timezone

import aiosqlite
import discord
from discord.ext import commands

from cogs.utils import DB, get_setting, snipe_cache

class Events(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── The ONE on_message ────────────────────────────────────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        # 1. Let discord.py process prefix commands first
        await self.bot.process_commands(message)

        if not message.guild:
            return

        # 2. AFK clear — if they sent a message, they're back
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT reason FROM afk WHERE user_id=? AND guild_id=?",
                (message.author.id, message.guild.id)
            ) as cur:
                was_afk = await cur.fetchone()
            if was_afk:
                await db.execute(
                    "DELETE FROM afk WHERE user_id=? AND guild_id=?",
                    (message.author.id, message.guild.id))
                await db.commit()
                try:
                    await message.channel.send(
                        f"👋 Welcome back {message.author.mention}! Removed your AFK.",
                        delete_after=8)
                except discord.Forbidden:
                    pass

        # 3. AFK notify — if they pinged someone who's AFK
        for mentioned in message.mentions:
            async with aiosqlite.connect(DB) as db:
                async with db.execute(
                    "SELECT reason, timestamp FROM afk WHERE user_id=? AND guild_id=?",
                    (mentioned.id, message.guild.id)
                ) as cur:
                    row = await cur.fetchone()
            if row:
                reason, ts = row
                try:
                    dt  = datetime.fromisoformat(ts)
                    rel = f"<t:{int(dt.timestamp())}:R>"
                except Exception:
                    rel = "a while ago"
                try:
                    await message.channel.send(
                        f"💤 **{mentioned.display_name}** is AFK: "
                        f"{reason or 'No reason'} (since {rel})",
                        delete_after=15)
                except discord.Forbidden:
                    pass

        # 4. Automod (returns True if message was deleted — skip further processing)
        automod_cog = self.bot.cogs.get("AutoMod")
        if automod_cog and await automod_cog.process(message):
            return

        # 5. Triggers
        trigger_cog = self.bot.cogs.get("Triggers")
        if trigger_cog:
            await trigger_cog.process(message)

        # 6. Custom commands (prefix-based, alias system)
        await self._run_custom_command(message)

        # 7. Groq chatbot — mention or #ai-chat
        if (self.bot.user in message.mentions or
                (hasattr(message.channel, 'name') and
                 message.channel.name.lower() == "ai-chat")):
            await self._groq_respond(message)

    # ── Custom command runner ─────────────────────────────────
    async def _run_custom_command(self, message: discord.Message):
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
        parts   = message.content[len(prefix):].split()
        if not parts:
            return
        invoked = parts[0].lower()

        # Don't run custom command if a real command matched
        if self.bot.get_command(invoked):
            return

        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT action_type, value FROM custom_commands"
                " WHERE guild_id=? AND name=?",
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
            # Build a fake message for the aliased command
            rest        = message.content[len(prefix) + len(invoked):].strip()
            fake_content = f"{prefix}{value} {rest}".strip()
            # Create a fake message object by mutating content via ctx
            ctx = await self.bot.get_context(message)
            ctx.message.content = fake_content
            new_ctx = await self.bot.get_context(ctx.message)
            if new_ctx.valid:
                await self.bot.invoke(new_ctx)

    # ── Groq response ─────────────────────────────────────────
    async def _groq_respond(self, message: discord.Message):
        fun_cog = self.bot.cogs.get("Fun")
        if not fun_cog:
            return
        async with message.channel.typing():
            try:
                reply = await asyncio.to_thread(
                    fun_cog.get_groq_response_fn,
                    message.author.id,
                    message.content
                )
                e = discord.Embed(description=reply[:4096], color=0x5865F2)
                e.set_footer(text=f"Asked by {message.author.display_name}")
                await message.reply(embed=e, mention_author=False)
            except Exception as ex:
                print(f"Groq on_message error: {ex}")
                await message.reply("brain broke for a sec, try again")

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

        if message.guild:
            e = discord.Embed(title="🗑️ Message Deleted", color=0xFF4444,
                              timestamp=datetime.now(timezone.utc))
            e.set_author(name=str(message.author),
                         icon_url=message.author.display_avatar.url)
            e.add_field(name="Channel", value=message.channel.mention)
            e.add_field(name="Content",
                        value=(message.content or "*empty*")[:1024], inline=False)
            await self._send_log(message.guild.id, 'log_message_id', e)

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message, after: discord.Message):
        if before.author.bot or not before.guild:
            return
        if before.content == after.content:
            return
        e = discord.Embed(title="✏️ Message Edited", color=0xFFAA00,
                          timestamp=datetime.now(timezone.utc))
        e.set_author(name=str(before.author),
                     icon_url=before.author.display_avatar.url)
        e.add_field(name="Channel", value=before.channel.mention)
        e.add_field(name="Before",  value=(before.content or "*empty*")[:512], inline=False)
        e.add_field(name="After",   value=(after.content  or "*empty*")[:512], inline=False)
        e.add_field(name="Jump",    value=f"[View]({after.jump_url})")
        await self._send_log(before.guild.id, 'log_message_id', e)

    # ── Member join / leave ───────────────────────────────────
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        gid = member.guild.id

        # Anti-raid
        enabled = await get_setting(gid, 'antiraid_enabled')
        if enabled:
            threshold = int(await get_setting(gid, 'antiraid_threshold') or 10)
            seconds   = int(await get_setting(gid, 'antiraid_seconds')   or 10)
            action    = await get_setting(gid, 'antiraid_action')        or 'slowmode'
            now       = datetime.now(timezone.utc).timestamp()
            tracker   = self.bot.join_tracker
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

        # Welcome
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
                try:
                    await ch.send(embed=e)
                except discord.Forbidden:
                    pass

        # Member join log
        e = discord.Embed(title="📥 Member Joined", color=0x57F287,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=member.display_avatar.url)
        e.add_field(name="User",        value=f"{member} ({member.mention})")
        e.add_field(name="Account Age", value=f"<t:{int(member.created_at.timestamp())}:R>")
        e.add_field(name="Members",     value=str(member.guild.member_count))
        await self._send_log(gid, 'log_member_id', e)

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
        await self._send_log(member.guild.id, 'log_member_id', e)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        gid = before.guild.id
        if before.nick != after.nick:
            e = discord.Embed(title="📝 Nickname Changed", color=0x5865F2,
                              timestamp=datetime.now(timezone.utc))
            e.set_thumbnail(url=after.display_avatar.url)
            e.add_field(name="User",   value=after.mention)
            e.add_field(name="Before", value=before.nick or "*none*")
            e.add_field(name="After",  value=after.nick  or "*none*")
            await self._send_log(gid, 'log_member_id', e)

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
            await self._send_log(gid, 'log_member_id', e)

    # ── Voice ─────────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member,
                                    before: discord.VoiceState,
                                    after:  discord.VoiceState):
        if before.channel == after.channel:
            return
        if before.channel is None:
            desc, color = f"📞 **{member}** joined **{after.channel.name}**", 0x57F287
        elif after.channel is None:
            desc, color = f"📴 **{member}** left **{before.channel.name}**", 0xFF4444
        else:
            desc, color = (f"🔀 **{member}** moved "
                           f"**{before.channel.name}** → **{after.channel.name}**", 0xFFAA00)
        e = discord.Embed(description=desc, color=color,
                          timestamp=datetime.now(timezone.utc))
        await self._send_log(member.guild.id, 'log_server_id', e)

    # ── Server events ─────────────────────────────────────────
    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        e = discord.Embed(title="✅ Channel Created", color=0x57F287,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Name", value=getattr(channel, 'mention', channel.name))
        e.add_field(name="Type", value=str(channel.type))
        await self._send_log(channel.guild.id, 'log_server_id', e)

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        e = discord.Embed(title="🗑️ Channel Deleted", color=0xFF4444,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="Name", value=f"#{channel.name}")
        e.add_field(name="Type", value=str(channel.type))
        await self._send_log(channel.guild.id, 'log_server_id', e)

    @commands.Cog.listener()
    async def on_invite_create(self, invite: discord.Invite):
        e = discord.Embed(title="🔗 Invite Created", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="By",      value=invite.inviter.mention if invite.inviter else "Unknown")
        e.add_field(name="Channel", value=invite.channel.mention if invite.channel else "Unknown")
        e.add_field(name="Code",    value=invite.code)
        e.add_field(name="Expires", value=(
            f"<t:{int(invite.expires_at.timestamp())}:R>"
            if invite.expires_at else "Never"))
        await self._send_log(invite.guild.id, 'log_server_id', e)

    # ── Starboard ─────────────────────────────────────────────
    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return
        gid = payload.guild_id

        # ── Bookmark (🔖) ─────────────────────────────────────
        if str(payload.emoji) == "🔖":
            channel = self.bot.get_channel(payload.channel_id)
            if not channel:
                return
            try:
                message = await channel.fetch_message(payload.message_id)
                user    = await self.bot.fetch_user(payload.user_id)
                async with aiosqlite.connect(DB) as db:
                    # Deduplicate: don't bookmark same message twice for same user
                    async with db.execute(
                        "SELECT 1 FROM bookmarks WHERE user_id=? AND message_id=?",
                        (user.id, message.id)
                    ) as cur:
                        if await cur.fetchone():
                            return
                    await db.execute(
                        "INSERT INTO bookmarks"
                        " (user_id,guild_id,message_id,channel_id,jump_url,content,author_name)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (user.id, gid, message.id, channel.id, message.jump_url,
                         message.content[:500], str(message.author.display_name)))
                    await db.commit()
                dm = discord.Embed(title="🔖 Message Bookmarked", color=0xFFD700,
                                   timestamp=datetime.now(timezone.utc))
                dm.add_field(name="From",    value=str(message.author.display_name))
                dm.add_field(name="Channel", value=f"#{channel.name}")
                dm.add_field(name="Content",
                             value=(message.content or "*no text*")[:300],
                             inline=False)
                dm.add_field(name="Jump", value=f"[Go to message]({message.jump_url})")
                dm.set_footer(text="Use /mybookmarks or ?mybookmarks to view all")
                await user.send(embed=dm)
            except (discord.Forbidden, discord.NotFound):
                pass
            except Exception as ex:
                print(f"Bookmark error: {ex}")
            return  # don't continue to starboard check

        # ── Starboard ─────────────────────────────────────────
        emoji     = await get_setting(gid, 'starboard_emoji') or '⭐'
        if str(payload.emoji) != emoji:
            return
        threshold = int(await get_setting(gid, 'starboard_threshold') or 3)
        sb_ch_id  = await get_setting(gid, 'starboard_channel_id')
        if not sb_ch_id:
            return

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

        # Mark posted immediately to prevent race condition
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR IGNORE INTO starboard_posted (guild_id,message_id) VALUES (?,?)",
                (gid, payload.message_id))
            await db.commit()

        sb_channel = self.bot.get_channel(sb_ch_id)
        if not sb_channel:
            return

        e = discord.Embed(color=0xFFD700, timestamp=message.created_at)
        e.set_author(name=str(message.author),
                     icon_url=message.author.display_avatar.url)
        if message.content:
            e.description = message.content[:2048]
        if message.attachments:
            e.set_image(url=message.attachments[0].url)
        e.add_field(name="Source", value=f"[Jump to message]({message.jump_url})")

        embeds = []
        # Include parent message if reply
        if message.reference:
            try:
                parent = await channel.fetch_message(message.reference.message_id)
                pe = discord.Embed(color=0xB8860B,
                                   description=f"**Replying to {parent.author.mention}:**"
                                               f"\n{parent.content[:512]}")
                pe.set_author(name=str(parent.author),
                              icon_url=parent.author.display_avatar.url)
                embeds.append(pe)
            except Exception:
                pass
        embeds.append(e)

        content = f"{emoji} **{reaction_count}** | {message.author.mention}"
        try:
            await sb_channel.send(
                content=content, embeds=embeds,
                allowed_mentions=discord.AllowedMentions(users=True))
        except discord.Forbidden:
            pass

    # ── Audit log for external bans/kicks ─────────────────────
    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, user: discord.User):
        """Log bans that happen outside the bot (manual bans)."""
        await asyncio.sleep(1)  # give audit log time to update
        try:
            async for entry in guild.audit_logs(
                limit=1, action=discord.AuditLogAction.ban
            ):
                if entry.target.id == user.id:
                    moderator = entry.user
                    if moderator.id == self.bot.user.id:
                        return  # bot did it, already logged
                    e = discord.Embed(title="🔨 Manual Ban (Audit Log)",
                                      color=0xCC0000,
                                      timestamp=datetime.now(timezone.utc))
                    e.add_field(name="User",   value=f"{user} ({user.mention})")
                    e.add_field(name="By",     value=moderator.mention)
                    e.add_field(name="Reason", value=entry.reason or "None", inline=False)
                    await self._send_log(guild.id, 'log_mod_id', e)
                    break
        except (discord.Forbidden, Exception):
            pass

    @commands.Cog.listener()
    async def on_member_unban(self, guild: discord.Guild, user: discord.User):
        e = discord.Embed(title="🔓 Member Unbanned", color=0x57F287,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="User", value=f"{user} ({user.mention})")
        await self._send_log(guild.id, 'log_mod_id', e)

    # ── Helpers ───────────────────────────────────────────────
    async def _send_log(self, guild_id: int, category: str,
                        embed: discord.Embed):
        ch_id = await get_setting(guild_id, category)
        if not ch_id:
            return
        ch = self.bot.get_channel(ch_id)
        if ch:
            try:
                await ch.send(embed=embed)
            except discord.Forbidden:
                pass

    async def _trigger_antiraid(self, guild: discord.Guild, action: str):
        log_id = (await get_setting(guild.id, 'log_server_id') or
                  await get_setting(guild.id, 'log_mod_id'))
        if log_id:
            ch = self.bot.get_channel(log_id)
            if ch:
                e = discord.Embed(title="🚨 Anti-Raid Triggered!", color=0xFF0000,
                                  timestamp=datetime.now(timezone.utc))
                e.add_field(name="Action", value=action)
                try:
                    await ch.send(embed=e)
                except discord.Forbidden:
                    pass

        if action == 'slowmode':
            for ch in guild.text_channels:
                try:
                    await ch.edit(slowmode_delay=60)
                except Exception:
                    pass

        elif action == 'lockdown':
            for ch in guild.text_channels:
                try:
                    ow = ch.overwrites_for(guild.default_role)
                    ow.send_messages = False
                    await ch.set_permissions(guild.default_role, overwrite=ow)
                except Exception:
                    pass

        elif action == 'kick_new':
            now = datetime.now(timezone.utc)
            for member in guild.members:
                if member.joined_at and (now - member.joined_at).total_seconds() < 30:
                    try:
                        await member.kick(reason="Anti-raid: new member during raid")
                    except Exception:
                        pass

    # ── Global prefix command error handler ───────────────────
    @commands.Cog.listener()
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.CommandNotFound):
            return  # silently ignore — might be a custom command
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ You don't have permission to do that.")
        elif isinstance(error, commands.BotMissingPermissions):
            await ctx.reply("❌ I'm missing permissions to do that.")
        elif isinstance(error, (commands.MemberNotFound, commands.UserNotFound)):
            await ctx.reply("❌ Member not found.")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.reply(f"❌ Missing argument: `{error.param.name}`")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply(f"❌ Bad argument: {error}")
        elif isinstance(error, commands.NoPrivateMessage):
            await ctx.reply("❌ This command can only be used in a server.")
        elif isinstance(error, commands.CheckFailure):
            await ctx.reply("❌ You can't use this command.")
        else:
            print(f"Unhandled command error [{ctx.command}]: {type(error).__name__}: {error}")

async def setup(bot):
    await bot.add_cog(Events(bot))
