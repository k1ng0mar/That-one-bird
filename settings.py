# cogs/settings.py — admin configuration & /setup
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from cogs.utils import DB, get_setting, set_setting

class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /setup ────────────────────────────────────────────────
    @app_commands.command(name="setup", description="View and configure all bot settings")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        g = interaction.guild

        async def ch(key):
            v = await get_setting(g.id, key)
            if not v: return "Not set"
            c = self.bot.get_channel(v)
            return c.mention if c else f"Unknown ({v})"

        async def ro(key):
            v = await get_setting(g.id, key)
            if not v: return "Not set"
            r = g.get_role(v)
            return r.mention if r else f"Unknown ({v})"

        prefix   = await get_setting(g.id, 'prefix') or "?"
        am_on    = await get_setting(g.id, 'automod_enabled')
        am_act   = await get_setting(g.id, 'automod_action') or "delete_only"
        ar_on    = await get_setting(g.id, 'antiraid_enabled')
        ar_th    = await get_setting(g.id, 'antiraid_threshold') or 10
        ar_s     = await get_setting(g.id, 'antiraid_seconds')   or 10
        ar_act   = await get_setting(g.id, 'antiraid_action')    or "slowmode"
        w_kick   = await get_setting(g.id, 'warn_kick_threshold') or 3
        w_ban    = await get_setting(g.id, 'warn_ban_threshold')  or 0
        w_mute   = await get_setting(g.id, 'warn_mute_threshold') or 0
        sb_em    = await get_setting(g.id, 'starboard_emoji')     or "⭐"
        sb_th    = await get_setting(g.id, 'starboard_threshold') or 3
        w_msg    = await get_setting(g.id, 'welcome_message')     or "Not set"

        e = discord.Embed(title=f"⚙️ Setup — {g.name}", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        if g.icon: e.set_thumbnail(url=g.icon.url)
        e.add_field(name="🔧 General",    value=f"Prefix: `{prefix}`", inline=False)
        e.add_field(name="📋 Log Channels",
                    value=f"Mod: {await ch('log_mod_id')}\n"
                          f"Messages: {await ch('log_message_id')}\n"
                          f"Members: {await ch('log_member_id')}\n"
                          f"Server: {await ch('log_server_id')}",
                    inline=False)
        e.add_field(name="👋 Welcome",
                    value=f"Channel: {await ch('welcome_channel_id')}\n"
                          f"Message: {w_msg[:60]}{'...' if len(w_msg)>60 else ''}",
                    inline=False)
        e.add_field(name="🎭 Roles",
                    value=f"Autorole: {await ro('autorole_id')}\n"
                          f"Jail Role: {await ro('jail_role_id')}\n"
                          f"Deadchat Ping: {await ro('deadchat_role_id')}\n"
                          f"Deadchat Perm: {await ro('deadchat_perm_role')}",
                    inline=False)
        e.add_field(name="🔒 Jail",
                    value=f"Channel: {await ch('jail_channel_id')}", inline=True)
        e.add_field(name="⭐ Starboard",
                    value=f"Channel: {await ch('starboard_channel_id')}\n"
                          f"{sb_em} × {sb_th}", inline=True)
        e.add_field(name="📖 Blood Trials",
                    value=f"Chapters: {await ch('chapter_channel_id')} "
                          f"(ping: {await ro('chapter_role_id')})\n"
                          f"Characters: {await ch('character_channel_id')}",
                    inline=False)
        e.add_field(name="🛡️ Automod",
                    value=f"{'✅' if am_on else '❌'} | Action: `{am_act}`", inline=True)
        e.add_field(name="🚨 Anti-Raid",
                    value=f"{'✅' if ar_on else '❌'} | {ar_th}/{ar_s}s → `{ar_act}`", inline=True)
        e.add_field(name="⚠️ Warn Thresholds",
                    value=f"Kick: {w_kick} | Ban: {w_ban or 'off'} | Mute: {w_mute or 'off'}",
                    inline=False)
        e.set_footer(text="Use /set* commands to change any setting")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── Prefix ────────────────────────────────────────────────
    @app_commands.command(name="setprefix", description="Change the command prefix")
    @app_commands.default_permissions(administrator=True)
    async def setprefix(self, interaction: discord.Interaction, prefix: str):
        await set_setting(interaction.guild.id, 'prefix', prefix)
        self.bot.prefix_cache[interaction.guild.id] = prefix
        await interaction.response.send_message(f"✅ Prefix → `{prefix}`", ephemeral=True)

    # ── Log channels ──────────────────────────────────────────
    @app_commands.command(name="setlogchannel", description="Set a log channel")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(category=[
        app_commands.Choice(name="mod",      value="log_mod_id"),
        app_commands.Choice(name="messages", value="log_message_id"),
        app_commands.Choice(name="members",  value="log_member_id"),
        app_commands.Choice(name="server",   value="log_server_id"),
    ])
    async def setlogchannel(self, interaction: discord.Interaction,
                            category: str, channel: discord.TextChannel):
        await set_setting(interaction.guild.id, category, channel.id)
        label = category.replace("log_","").replace("_id","")
        await interaction.response.send_message(
            f"✅ **{label}** logs → {channel.mention}", ephemeral=True)

    # ── Welcome ───────────────────────────────────────────────
    @app_commands.command(name="setwelcome", description="Set welcome channel and message")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(message="Use {user} {name} {server} {count}")
    async def setwelcome(self, interaction: discord.Interaction,
                         channel: discord.TextChannel, message: str):
        await set_setting(interaction.guild.id, 'welcome_channel_id', channel.id)
        await set_setting(interaction.guild.id, 'welcome_message', message)
        preview = (message
                   .replace("{user}",   interaction.user.mention)
                   .replace("{name}",   interaction.user.display_name)
                   .replace("{server}", interaction.guild.name)
                   .replace("{count}",  str(interaction.guild.member_count)))
        e = discord.Embed(title="✅ Welcome set — Preview:", description=preview, color=0x57F287)
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── Autorole / jail / deadchat ────────────────────────────
    @app_commands.command(name="setautorole", description="Auto-assign role to new members")
    @app_commands.default_permissions(administrator=True)
    async def setautorole(self, i: discord.Interaction, role: discord.Role):
        await set_setting(i.guild.id, 'autorole_id', role.id)
        await i.response.send_message(f"✅ Autorole → {role.mention}", ephemeral=True)

    @app_commands.command(name="setjail", description="Set jail channel and role")
    @app_commands.default_permissions(administrator=True)
    async def setjail(self, i: discord.Interaction,
                      channel: discord.TextChannel, role: discord.Role):
        await set_setting(i.guild.id, 'jail_channel_id', channel.id)
        await set_setting(i.guild.id, 'jail_role_id', role.id)
        await i.response.send_message(
            f"✅ Jail → {channel.mention} | Role → {role.mention}", ephemeral=True)

    @app_commands.command(name="setdeadchatrole", description="Role pinged by /deadchat")
    @app_commands.default_permissions(administrator=True)
    async def setdeadchatrole(self, i: discord.Interaction, role: discord.Role):
        await set_setting(i.guild.id, 'deadchat_role_id', role.id)
        await i.response.send_message(f"✅ Deadchat ping → {role.mention}", ephemeral=True)

    @app_commands.command(name="setdeadchatperm", description="Restrict who can use /deadchat")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="Leave empty to allow everyone")
    async def setdeadchatperm(self, i: discord.Interaction, role: discord.Role = None):
        await set_setting(i.guild.id, 'deadchat_perm_role', role.id if role else None)
        await i.response.send_message(
            f"✅ Deadchat restricted to {role.mention}." if role
            else "✅ Deadchat open to everyone.", ephemeral=True)

    # ── Starboard ─────────────────────────────────────────────
    @app_commands.command(name="setstarboard", description="Configure the starboard")
    @app_commands.default_permissions(administrator=True)
    async def setstarboard(self, i: discord.Interaction,
                           channel: discord.TextChannel,
                           emoji: str = "⭐", threshold: int = 3):
        await set_setting(i.guild.id, 'starboard_channel_id', channel.id)
        await set_setting(i.guild.id, 'starboard_emoji',      emoji)
        await set_setting(i.guild.id, 'starboard_threshold',  threshold)
        await i.response.send_message(
            f"✅ Starboard → {channel.mention} | {emoji} × {threshold}", ephemeral=True)

    # ── Blood Trials ──────────────────────────────────────────
    @app_commands.command(name="setchapterchannel", description="Channel for chapter announcements")
    @app_commands.default_permissions(administrator=True)
    async def setchapterchannel(self, i: discord.Interaction,
                                channel: discord.TextChannel, role: discord.Role = None):
        await set_setting(i.guild.id, 'chapter_channel_id', channel.id)
        if role:
            await set_setting(i.guild.id, 'chapter_role_id', role.id)
        await i.response.send_message(
            f"✅ Chapters → {channel.mention}" + (f" | Ping: {role.mention}" if role else ""),
            ephemeral=True)

    @app_commands.command(name="setcharacterchannel", description="Channel for character announcements")
    @app_commands.default_permissions(administrator=True)
    async def setcharacterchannel(self, i: discord.Interaction, channel: discord.TextChannel):
        await set_setting(i.guild.id, 'character_channel_id', channel.id)
        await i.response.send_message(f"✅ Characters → {channel.mention}", ephemeral=True)

    # ── Cooldowns ─────────────────────────────────────────────
    @app_commands.command(name="setcooldown", description="Override command cooldown in seconds")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(command=[
        app_commands.Choice(name=c, value=c)
        for c in ["deadchat","meme","roast","8ball","poll","urban"]
    ])
    async def setcooldown(self, i: discord.Interaction, command: str, seconds: int):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO cooldowns (guild_id,command,seconds) VALUES (?,?,?)",
                (i.guild.id, command, max(0, seconds)))
            await db.commit()
        await i.response.send_message(
            f"✅ `/{command}` cooldown → **{seconds}s**", ephemeral=True)

    # ── Command permissions ───────────────────────────────────
    @app_commands.command(name="setpermission", description="Control who can use a command")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        command="Command name (no prefix/slash)",
        role="Required role (empty = everyone)",
        silent="Silently ignore unauthorized uses"
    )
    async def setpermission(self, i: discord.Interaction,
                            command: str, role: discord.Role = None,
                            silent: bool = False):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO command_perms (guild_id,command,role_id,silent)"
                " VALUES (?,?,?,?)",
                (i.guild.id, command, role.id if role else 0, int(silent)))
            await db.commit()
        await i.response.send_message(
            f"✅ `{command}` → {role.mention if role else 'everyone'}"
            f" | Silent: {'yes' if silent else 'no'}",
            ephemeral=True)

    # ── Command display mode ──────────────────────────────────
    @app_commands.command(name="setdisplay",
                          description="Set whether a command response is public, ephemeral, or auto-deleted")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        command="Command name",
        mode="How the response appears",
        seconds="If timed: delete after how many seconds"
    )
    @app_commands.choices(mode=[
        app_commands.Choice(name="public — visible to everyone",         value="public"),
        app_commands.Choice(name="ephemeral — only the user sees it",    value="ephemeral"),
        app_commands.Choice(name="timed — posted then deleted",          value="timed"),
    ])
    async def setdisplay(self, i: discord.Interaction,
                         command: str, mode: str, seconds: int = 5):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO command_display (guild_id,command,mode,seconds)"
                " VALUES (?,?,?,?)",
                (i.guild.id, command, mode, seconds))
            await db.commit()
        detail = f" (delete after {seconds}s)" if mode == "timed" else ""
        await i.response.send_message(
            f"✅ `{command}` display → **{mode}**{detail}", ephemeral=True)

    # ── Anti-raid ─────────────────────────────────────────────
    @app_commands.command(name="antiraidsettings", description="Configure anti-raid")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="slowmode (60s)", value="slowmode"),
        app_commands.Choice(name="lockdown",       value="lockdown"),
        app_commands.Choice(name="kick new",       value="kick_new"),
    ])
    async def antiraidsettings(self, i: discord.Interaction,
                               threshold: int, seconds: int, action: str):
        await set_setting(i.guild.id, 'antiraid_threshold', threshold)
        await set_setting(i.guild.id, 'antiraid_seconds',   seconds)
        await set_setting(i.guild.id, 'antiraid_action',    action)
        await i.response.send_message(
            f"✅ Anti-raid: **{threshold}** joins in **{seconds}s** → `{action}`",
            ephemeral=True)

    @app_commands.command(name="antiraidtoggle", description="Enable/disable anti-raid")
    @app_commands.default_permissions(administrator=True)
    async def antiraidtoggle(self, i: discord.Interaction):
        cur = await get_setting(i.guild.id, 'antiraid_enabled') or 0
        new = 1 - int(cur)
        await set_setting(i.guild.id, 'antiraid_enabled', new)
        await i.response.send_message(
            f"✅ Anti-raid {'**enabled**' if new else '**disabled**'}.", ephemeral=True)

    # ── Warn thresholds ───────────────────────────────────────
    @app_commands.command(name="setwarnthreshold",
                          description="Set what happens at a certain warn count")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="kick", value="kick"),
        app_commands.Choice(name="ban",  value="ban"),
        app_commands.Choice(name="mute", value="mute"),
    ])
    @app_commands.describe(action="Action to trigger", count="Warn count (0 = disabled)",
                           mute_minutes="Minutes to mute (if action is mute)")
    async def setwarnthreshold(self, i: discord.Interaction,
                               action: str, count: int, mute_minutes: int = 10):
        key = f"warn_{action}_threshold"
        await set_setting(i.guild.id, key, count)
        if action == "mute":
            await set_setting(i.guild.id, 'warn_mute_minutes', mute_minutes)
        await i.response.send_message(
            f"✅ At **{count}** warns → `{action}`" +
            (f" for {mute_minutes}min" if action == "mute" else "") +
            (" (disabled)" if count == 0 else ""),
            ephemeral=True)

async def setup(bot):
    await bot.add_cog(Settings(bot))
