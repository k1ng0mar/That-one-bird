# cogs/automod.py — configurable word filter + actions

import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite
from datetime import datetime, timezone

from cogs.utils import DB, get_setting, set_setting, add_warn, try_dm, fetch_member, parse_duration
from cogs.utils import log_action
from datetime import timedelta

class AutoMod(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def get_words(self, guild_id: int) -> set:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT word FROM automod_words WHERE guild_id=?", (guild_id,)
            ) as cur:
                rows = await cur.fetchall()
        return {row[0].lower() for row in rows}

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        enabled = await get_setting(message.guild.id, 'automod_enabled')
        if not enabled:
            return

        words   = await self.get_words(message.guild.id)
        content = message.content.lower()
        if not any(w in content for w in words):
            return

        try:
            await message.delete()
        except discord.Forbidden:
            pass

        action = await get_setting(message.guild.id, 'automod_action') or 'delete_only'

        if action == 'delete_only':
            await message.channel.send(
                f"{message.author.mention} ⚠️ That message was removed.",
                delete_after=8)

        elif action == 'warn':
            expiry_str = await get_setting(message.guild.id, 'automod_warn_expiry')
            delta      = parse_duration(expiry_str) if expiry_str else None
            expires_at = datetime.now(timezone.utc) + delta if delta else None
            count = await add_warn(
                message.author.id, message.guild.id, self.bot.user.id,
                "Automod: filtered word", expires_at)
            await log_action(self.bot, "Automod Warn", message.author,
                             self.bot.user, "Filtered word", message.guild.id)
            await message.channel.send(
                f"{message.author.mention} ⚠️ Watch your language! You now have **{count}** warn(s).",
                delete_after=10)
            dm = discord.Embed(title="⚠️ Automod Warning", color=0xFFAA00)
            dm.add_field(name="Server", value=message.guild.name)
            dm.add_field(name="Reason", value="Filtered word in message")
            dm.add_field(name="Total Warns", value=str(count), inline=False)
            await try_dm(message.author, dm)

        elif action == 'mute':
            minutes = await get_setting(message.guild.id, 'automod_mute_minutes') or 10
            try:
                member = await fetch_member(self.bot, message.guild.id, message.author.id)
                until  = datetime.now(timezone.utc) + timedelta(minutes=int(minutes))
                await member.edit(timed_out_until=until, reason="Automod: filtered word")
                await log_action(self.bot, f"Automod Mute ({minutes}min)", message.author,
                                 self.bot.user, "Filtered word", message.guild.id)
                await message.channel.send(
                    f"{message.author.mention} 🔇 You've been muted for **{minutes}min** for using a filtered word.",
                    delete_after=10)
                dm = discord.Embed(title="🔇 Automod Mute", color=0xFF8800)
                dm.add_field(name="Server",   value=message.guild.name)
                dm.add_field(name="Duration", value=f"{minutes} minutes")
                dm.add_field(name="Reason",   value="Filtered word in message")
                await try_dm(message.author, dm)
            except Exception as ex:
                print(f"Automod mute error: {ex}")

    # ── /automod group ────────────────────────────────────────
    automod = app_commands.Group(name="automod", description="Configure automod settings")

    @automod.command(name="toggle", description="Enable or disable automod")
    @app_commands.default_permissions(administrator=True)
    async def toggle(self, interaction: discord.Interaction):
        current = await get_setting(interaction.guild.id, 'automod_enabled') or 0
        new = 1 - int(current)
        await set_setting(interaction.guild.id, 'automod_enabled', new)
        await interaction.response.send_message(
            f"✅ Automod {'**enabled**' if new else '**disabled**'}.", ephemeral=True)

    @automod.command(name="addword", description="Add a word to the filter")
    @app_commands.default_permissions(administrator=True)
    async def addword(self, interaction: discord.Interaction, word: str):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT OR IGNORE INTO automod_words (guild_id, word) VALUES (?,?)",
                (interaction.guild.id, word.lower()))
            await db.commit()
        await interaction.response.send_message(
            f"✅ Added `{word.lower()}` to filter.", ephemeral=True)

    @automod.command(name="removeword", description="Remove a word from the filter")
    @app_commands.default_permissions(administrator=True)
    async def removeword(self, interaction: discord.Interaction, word: str):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "DELETE FROM automod_words WHERE guild_id=? AND word=?",
                (interaction.guild.id, word.lower()))
            await db.commit()
        if cur.rowcount:
            await interaction.response.send_message(
                f"🗑️ Removed `{word.lower()}` from filter.", ephemeral=True)
        else:
            await interaction.response.send_message(
                f"❌ `{word}` wasn't in the filter.", ephemeral=True)

    @automod.command(name="listwords", description="View all filtered words")
    @app_commands.default_permissions(administrator=True)
    async def listwords(self, interaction: discord.Interaction):
        words = await self.get_words(interaction.guild.id)
        if not words:
            await interaction.response.send_message("No words in filter.", ephemeral=True)
            return
        e = discord.Embed(title=f"🚫 Filtered Words ({len(words)})", color=0xFF4444)
        e.description = "||" + "||, ||".join(sorted(words)) + "||"
        await interaction.response.send_message(embed=e, ephemeral=True)

    @automod.command(name="setaction", description="Set what happens when a filtered word is detected")
    @app_commands.default_permissions(administrator=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="delete only",        value="delete_only"),
        app_commands.Choice(name="warn the user",      value="warn"),
        app_commands.Choice(name="mute the user",      value="mute"),
    ])
    async def setaction(self, interaction: discord.Interaction, action: str):
        await set_setting(interaction.guild.id, 'automod_action', action)
        await interaction.response.send_message(
            f"✅ Automod action → `{action}`", ephemeral=True)

    @automod.command(name="setmuteduration", description="Mute duration in minutes (for mute action)")
    @app_commands.default_permissions(administrator=True)
    async def setmuteduration(self, interaction: discord.Interaction, minutes: int):
        await set_setting(interaction.guild.id, 'automod_mute_minutes', minutes)
        await interaction.response.send_message(
            f"✅ Automod mute duration → **{minutes}min**", ephemeral=True)

    @automod.command(name="setwarnexpiry", description="Warn expiry for automod warns (e.g. 7d, 24h)")
    @app_commands.default_permissions(administrator=True)
    async def setwarnexpiry(self, interaction: discord.Interaction, duration: str):
        if not parse_duration(duration):
            await interaction.response.send_message(
                "❌ Invalid format. Use e.g. `7d`, `24h`, `30m`.", ephemeral=True)
            return
        await set_setting(interaction.guild.id, 'automod_warn_expiry', duration)
        await interaction.response.send_message(
            f"✅ Automod warn expiry → **{duration}**", ephemeral=True)

async def setup(bot):
    await bot.add_cog(AutoMod(bot))
