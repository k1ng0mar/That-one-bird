# cogs/triggers.py — custom text/image/GIF triggers
import discord
from discord import app_commands
from discord.ext import commands
import aiosqlite

from cogs.utils import DB, is_url

class Triggers(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get_triggers(self, guild_id: int) -> list:
        async with aiosqlite.connect(DB) as db:
            async with db.execute(
                "SELECT id,trigger,response,match_type FROM triggers"
                " WHERE guild_id=? ORDER BY id",
                (guild_id,)
            ) as cur:
                return await cur.fetchall()

    async def process(self, message: discord.Message) -> bool:
        """Called by events.py central on_message. Returns True if triggered."""
        if not message.guild:
            return False
        content  = message.content.lower()
        triggers = await self._get_triggers(message.guild.id)
        for _, trigger, response, match_type in triggers:
            matched = (
                (match_type == 'startswith' and content.startswith(trigger)) or
                (match_type == 'contains'   and trigger in content)
            )
            if matched:
                if is_url(response):
                    e = discord.Embed(color=0x5865F2)
                    e.set_image(url=response.strip())
                    await message.channel.send(embed=e)
                else:
                    await message.channel.send(response)
                return True
        return False

    @app_commands.command(name="settrigger", description="Add a trigger → response")
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(
        trigger="Word/phrase that activates the response",
        response="Text, image URL, or GIF URL",
        match_type="Where to match"
    )
    @app_commands.choices(match_type=[
        app_commands.Choice(name="contains",   value="contains"),
        app_commands.Choice(name="startswith", value="startswith"),
    ])
    async def settrigger(self, i: discord.Interaction,
                         trigger: str, response: str,
                         match_type: str = "contains"):
        async with aiosqlite.connect(DB) as db:
            await db.execute(
                "INSERT INTO triggers (guild_id,trigger,response,match_type)"
                " VALUES (?,?,?,?)",
                (i.guild.id, trigger.lower(), response, match_type))
            await db.commit()
        e = discord.Embed(title="✅ Trigger added", color=0x57F287)
        e.add_field(name="Trigger",  value=f"`{trigger}`")
        e.add_field(name="Match",    value=match_type)
        e.add_field(name="Response", value=response[:200], inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="listtriggers", description="List all triggers")
    @app_commands.default_permissions(manage_messages=True)
    async def listtriggers(self, i: discord.Interaction):
        rows = await self._get_triggers(i.guild.id)
        if not rows:
            await i.response.send_message("No triggers set.", ephemeral=True); return
        e = discord.Embed(title=f"⚡ Triggers ({len(rows)})", color=0x5865F2)
        for tid, trigger, response, match_type in rows:
            e.add_field(
                name=f"#{tid} — `{trigger}` [{match_type}]",
                value=response[:100] + ("..." if len(response) > 100 else ""),
                inline=False)
        await i.response.send_message(embed=e, ephemeral=True)

    @app_commands.command(name="deletetrigger", description="Delete a trigger by ID")
    @app_commands.default_permissions(manage_messages=True)
    async def deletetrigger(self, i: discord.Interaction, trigger_id: int):
        async with aiosqlite.connect(DB) as db:
            cur = await db.execute(
                "DELETE FROM triggers WHERE id=? AND guild_id=?",
                (trigger_id, i.guild.id))
            await db.commit()
        if cur.rowcount:
            await i.response.send_message(f"🗑️ Trigger `#{trigger_id}` deleted.", ephemeral=True)
        else:
            await i.response.send_message("❌ Not found.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Triggers(bot))
