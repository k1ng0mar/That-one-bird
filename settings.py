# cogs/settings.py — admin configuration & /setup

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timezone

from cogs.utils import get_setting, set_setting, DB

class Settings(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ── /setup ────────────────────────────────────────────────
    @app_commands.command(name="setup", description="View and configure all bot settings (admin only)")
    @app_commands.default_permissions(administrator=True)
    async def setup(self, interaction: discord.Interaction):
        g = interaction.guild

        async def val(key):
            v = await get_setting(g.id, key)
            if v is None:
                return "Not set"
            ch = self.bot.get_channel(v)
            if ch:
                return ch.mention
            role = g.get_role(v)
            if role:
                return role.mention
            return str(v)

        prefix      = await get_setting(g.id, 'prefix') or "?"
        log_mod     = await val('log_mod_id')
        log_msg     = await val('log_message_id')
        log_mem     = await val('log_member_id')
        log_srv     = await val('log_server_id')
        autorole    = await val('autorole_id')
        wc          = await val('welcome_channel_id')
        wmsg        = await get_setting(g.id, 'welcome_message') or "Not set"
        jail_ch     = await val('jail_channel_id')
        jail_role   = await val('jail_role_id')
        dc_role     = await val('deadchat_role_id')
        dc_perm     = await val('deadchat_perm_role')
        sb_ch       = await val('starboard_channel_id')
        sb_emoji    = await get_setting(g.id, 'starboard_emoji') or "⭐"
        sb_thresh   = await get_setting(g.id, 'starboard_threshold') or 3
        chap_ch     = await val('chapter_channel_id')
        chap_role   = await val('chapter_role_id')
        char_ch     = await val('character_channel_id')
        am_enabled  = await get_setting(g.id, 'automod_enabled')
        am_action   = await get_setting(g.id, 'automod_action') or "delete_only"
        ar_enabled  = await get_setting(g.id, 'antiraid_enabled')
        ar_thresh   = await get_setting(g.id, 'antiraid_threshold') or 10
        ar_secs     = await get_setting(g.id, 'antiraid_seconds') or 10
        ar_action   = await get_setting(g.id, 'antiraid_action') or "slowmode"

        e = discord.Embed(title=f"⚙️ Setup — {g.name}", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        e.set_thumbnail(url=g.icon.url if g.icon else None)
        e.add_field(name="🔧 General",
                    value=f"**Prefix:** `{prefix}`", inline=False)
        e.add_field(name="📋 Log Channels",
                    value=f"Mod: {log_mod}\nMessages: {log_msg}\nMembers: {log_mem}\nServer: {log_srv}",
                    inline=False)
        e.add_field(name="👋 Welcome",
                    value=f"Channel: {wc}\nMessage: {wmsg[:60]}{'...' if len(wmsg)>60 else ''}",
                    inline=False)
        e.add_field(name="🎭 Roles",
                    value=f"Autorole: {autorole}\nJail Role: {jail_role}\nDeadchat Ping: {dc_role}\nDeadchat Perm: {dc_perm}",
                    inline=False)
        e.add_field(name="🔒 Jail",     value=f"Channel: {jail_ch}", inline=True)
        e.add_field(name="⭐ Starboard", value=f"Channel: {sb_ch}\nEmoji: {sb_emoji} | Threshold: {sb_thresh}", inline=True)
        e.add_field(name="📖 Blood Trials",
                    value=f"Chapters: {chap_ch} (ping: {chap_role})\nCharacters: {char_ch}",
                    inline=False)
        e.add_field(name="🛡️ Automod",
                    value=f"Enabled: {'✅' if am_enabled else '❌'} | Action: `{am_action}`",
                    inline=True)
        e.add_field(name="🚨 Anti-Raid",
                    value=f"Enabled: {'✅' if ar_enabled else '❌'} | {ar_thresh} joins/{ar_secs}s → `{ar_action}`",
                    inline=True)
        e.set_footer(text="Use the /set* commands to change any setting")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── Prefix ────────────────────────────────────────────────
    @app_commands.command(name="setprefix", description="Change the bot's command prefix")
    @app_commands.default_permissions(administrator=True)
    async def setprefix(self, interaction: discord.Interaction, prefix: str):
        await set_setting(interaction.guild.id, 'prefix', prefix)
        self.bot.prefix_cache[interaction.guild.id] = prefix
        await interaction.response.send_message(
            f"✅ Prefix set to `{prefix}`", ephemeral=True)

    # ── Log channels ──────────────────────────────────────────
    @app_commands.command(name="setlogchannel", description="Set a log channel for a category")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(category="Log category", channel="Channel to send logs to")
    @app_commands.choices(category=[
        app_commands.Choice(name="mod (bans/kicks/warns/mutes/jails)", value="log_mod_id"),
        app_commands.Choice(name="messages (edits/deletes)",           value="log_message_id"),
        app_commands.Choice(name="members (joins/leaves/roles/nicks)", value="log_member_id"),
        app_commands.Choice(name="server (channels/voice/invites)",    value="log_server_id"),
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
    @app_commands.describe(
        channel="Channel for welcome messages",
        message="Use {user} {name} {server} {count} as variables"
    )
    async def setwelcome(self, interaction: discord.Interaction,
                         channel: discord.TextChannel, message: str):
        await set_setting(interaction.guild.id, 'welcome_channel_id', channel.id)
        await set_setting(interaction.guild.id, 'welcome_message', message)
        preview = (message
                   .replace("{user}",   interaction.user.mention)
                   .replace("{name}",   interaction.user.display_name)
                   .replace("{server}", interaction.guild.name)
                   .replace("{count}",  str(interaction.guild.member_count)))
        e = discord.Embed(title="✅ Welcome set! Preview:", description=preview, color=0x57F287)
        e.set_footer(text=f"Sending to #{channel.name}")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ── Autorole ──────────────────────────────────────────────
    @app_commands.command(name="setautorole", description="Auto-assign a role to new members")
    @app_commands.default_permissions(administrator=True)
    async def setautorole(self, interaction: discord.Interaction, role: discord.Role):
        await set_setting(interaction.guild.id, 'autorole_id', role.id)
        await interaction.response.send_message(f"✅ Autorole → {role.mention}", ephemeral=True)

    # ── Jail ──────────────────────────────────────────────────
    @app_commands.command(name="setjail", description="Set the jail channel and role")
    @app_commands.default_permissions(administrator=True)
    async def setjail(self, interaction: discord.Interaction,
                      channel: discord.TextChannel, role: discord.Role):
        await set_setting(interaction.guild.id, 'jail_channel_id', channel.id)
        await set_setting(interaction.guild.id, 'jail_role_id', role.id)
        await interaction.response.send_message(
            f"✅ Jail channel → {channel.mention} | Jail role → {role.mention}", ephemeral=True)

    # ── Deadchat ──────────────────────────────────────────────
    @app_commands.command(name="setdeadchatrole", description="Set the role pinged by /deadchat")
    @app_commands.default_permissions(administrator=True)
    async def setdeadchatrole(self, interaction: discord.Interaction, role: discord.Role):
        await set_setting(interaction.guild.id, 'deadchat_role_id', role.id)
        await interaction.response.send_message(f"✅ Deadchat ping → {role.mention}", ephemeral=True)

    @app_commands.command(name="setdeadchatperm", description="Restrict who can use /deadchat")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(role="Leave empty to allow everyone")
    async def setdeadchatperm(self, interaction: discord.Interaction, role: discord.Role = None):
        await set_setting(interaction.guild.id, 'deadchat_perm_role', role.id if role else None)
        msg = f"✅ Deadchat restricted to {role.mention}." if role else "✅ Deadchat open to everyone."
        await interaction.response.send_message(msg, ephemeral=True)

    # ── Starboard ─────────────────────────────────────────────
    @app_commands.command(name="setstarboard", description="Configure the starboard")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(channel="Starboard channel", emoji="Reaction emoji to watch", threshold="Reactions needed")
    async def setstarboard(self, interaction: discord.Interaction,
                           channel: discord.TextChannel,
                           emoji: str = "⭐", threshold: int = 3):
        await set_setting(interaction.guild.id, 'starboard_channel_id', channel.id)
        await set_setting(interaction.guild.id, 'starboard_emoji', emoji)
        await set_setting(interaction.guild.id, 'starboard_threshold', threshold)
        await interaction.response.send_message(
            f"✅ Starboard → {channel.mention} | {emoji} × {threshold}", ephemeral=True)

    # ── Blood Trials ──────────────────────────────────────────
    @app_commands.command(name="setchapterchannel", description="Set channel for Blood Trials chapter announcements")
    @app_commands.default_permissions(administrator=True)
    async def setchapterchannel(self, interaction: discord.Interaction,
                                channel: discord.TextChannel, role: discord.Role = None):
        await set_setting(interaction.guild.id, 'chapter_channel_id', channel.id)
        if role:
            await set_setting(interaction.guild.id, 'chapter_role_id', role.id)
        await interaction.response.send_message(
            f"✅ Chapter announcements → {channel.mention}" +
            (f" | Ping: {role.mention}" if role else ""), ephemeral=True)

    @app_commands.command(name="setcharacterchannel", description="Set channel for Blood Trials character announcements")
    @app_commands.default_permissions(administrator=True)
    async def setcharacterchannel(self, interaction: discord.Interaction,
                                  channel: discord.TextChannel):
        await set_setting(interaction.guild.id, 'character_channel_id', channel.id)
        await interaction.response.send_message(
            f"✅ Character announcements → {channel.mention}", ephemeral=True)

    # ── Cooldowns ─────────────────────────────────────────────
    @app_commands.command(name="setcooldown", description="Override cooldown for a command (seconds)")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(command=[
        app_commands.Choice(name="deadchat", value="deadchat"),
        app_commands.Choice(name="meme",     value="meme"),
        app_commands.Choice(name="roast",    value="roast"),
        app_commands.Choice(name="8ball",    value="8ball"),
        app_commands.Choice(name="poll",     value="poll"),
    ])
    async def setcooldown(self, interaction: discord.Interaction, command: str, seconds: int):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO cooldowns (guild_id,command,seconds) VALUES (?,?,?)",
                (interaction.guild.id, command, max(0, seconds))
            )
            await db.commit()
        await interaction.response.send_message(
            f"✅ `/{command}` cooldown → **{seconds}s**", ephemeral=True)

    # ── Command permissions ───────────────────────────────────
    @app_commands.command(name="setpermission",
                          description="Control who can use a command")
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(
        command="Command name (without prefix/slash)",
        role="Role required (leave empty = everyone)",
        silent="If true, silently ignore unauthorized uses"
    )
    async def setpermission(self, interaction: discord.Interaction,
                            command: str, role: discord.Role = None, silent: bool = False):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR REPLACE INTO command_perms (guild_id,command,role_id,silent) VALUES (?,?,?,?)",
                (interaction.guild.id, command, role.id if role else 0, int(silent))
            )
            await db.commit()
        who = role.mention if role else "everyone"
        await interaction.response.send_message(
            f"✅ `{command}` → {who} | Silent fail: {'yes' if silent else 'no'}",
            ephemeral=True)

    # ── Anti-raid ─────────────────────────────────────────────
    @app_commands.command(name="antiraidsettings", description="Configure anti-raid settings")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="slowmode (60s)",  value="slowmode"),
        app_commands.Choice(name="lockdown",        value="lockdown"),
        app_commands.Choice(name="kick new members",value="kick_new"),
    ])
    async def antiraidsettings(self, interaction: discord.Interaction,
                               threshold: int, seconds: int, action: str):
        await set_setting(interaction.guild.id, 'antiraid_threshold', threshold)
        await set_setting(interaction.guild.id, 'antiraid_seconds',   seconds)
        await set_setting(interaction.guild.id, 'antiraid_action',    action)
        await interaction.response.send_message(
            f"✅ Anti-raid: **{threshold}** joins in **{seconds}s** → `{action}`",
            ephemeral=True)

    @app_commands.command(name="antiraidtoggle", description="Enable or disable anti-raid")
    @app_commands.default_permissions(administrator=True)
    async def antiraidtoggle(self, interaction: discord.Interaction):
        current = await get_setting(interaction.guild.id, 'antiraid_enabled') or 0
        new = 1 - int(current)
        await set_setting(interaction.guild.id, 'antiraid_enabled', new)
        await interaction.response.send_message(
            f"✅ Anti-raid {'**enabled**' if new else '**disabled**'}.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Settings(bot))
