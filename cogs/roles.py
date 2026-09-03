# cogs/roles.py — role management commands
from datetime import datetime, timezone
import discord
from discord import app_commands
from discord.ext import commands

from cogs.utils import log_action

class Roles(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    roles_group = app_commands.Group(name="role", description="Role management commands")

    @roles_group.command(name="add", description="Add a role to a member")
    @app_commands.default_permissions(manage_roles=True)
    async def role_add(self, i: discord.Interaction,
                       member: discord.Member, role: discord.Role):
        if role >= i.guild.me.top_role:
            await i.response.send_message(
                "❌ That role is higher than or equal to my top role.", ephemeral=True); return
        if role in member.roles:
            await i.response.send_message(
                f"❌ {member.mention} already has {role.mention}.", ephemeral=True); return
        await member.add_roles(role, reason=f"Role added by {i.user}")
        await log_action(self.bot, "Role Add", member, i.user,
                         f"+{role.name}", i.guild.id)
        await i.response.send_message(embed=discord.Embed(
            description=f"✅ Added {role.mention} to {member.mention}.",
            color=0x57F287))

    @roles_group.command(name="remove", description="Remove a role from a member")
    @app_commands.default_permissions(manage_roles=True)
    async def role_remove(self, i: discord.Interaction,
                          member: discord.Member, role: discord.Role):
        if role >= i.guild.me.top_role:
            await i.response.send_message(
                "❌ That role is higher than or equal to my top role.", ephemeral=True); return
        if role not in member.roles:
            await i.response.send_message(
                f"❌ {member.mention} doesn't have {role.mention}.", ephemeral=True); return
        await member.remove_roles(role, reason=f"Role removed by {i.user}")
        await log_action(self.bot, "Role Remove", member, i.user,
                         f"-{role.name}", i.guild.id)
        await i.response.send_message(embed=discord.Embed(
            description=f"✅ Removed {role.mention} from {member.mention}.",
            color=0x57F287))

    @roles_group.command(name="info", description="Info about a role")
    async def role_info(self, i: discord.Interaction, role: discord.Role):
        members_with_role = len(role.members)
        perms = [p.replace("_", " ").title()
                 for p, v in role.permissions if v][:8]
        e = discord.Embed(title=f"🎭 {role.name}", color=role.color,
                          timestamp=datetime.now(timezone.utc))
        e.add_field(name="ID",         value=str(role.id))
        e.add_field(name="Color",      value=str(role.color))
        e.add_field(name="Members",    value=str(members_with_role))
        e.add_field(name="Mentionable",value="Yes" if role.mentionable else "No")
        e.add_field(name="Hoisted",    value="Yes" if role.hoist else "No")
        e.add_field(name="Position",   value=str(role.position))
        e.add_field(name="Created",    value=f"<t:{int(role.created_at.timestamp())}:R>")
        if perms:
            e.add_field(name="Key Permissions",
                        value=", ".join(perms), inline=False)
        await i.response.send_message(embed=e)

    @roles_group.command(name="list", description="List all server roles")
    async def role_list(self, i: discord.Interaction):
        roles = sorted(i.guild.roles[1:], key=lambda r: r.position, reverse=True)
        chunks = []
        current = ""
        for r in roles:
            line = f"{r.mention} `{len(r.members)}`\n"
            if len(current) + len(line) > 1000:
                chunks.append(current)
                current = line
            else:
                current += line
        if current:
            chunks.append(current)
        e = discord.Embed(title=f"🎭 Roles ({len(roles)})", color=0x5865F2,
                          timestamp=datetime.now(timezone.utc))
        for i_chunk, chunk in enumerate(chunks[:8]):
            e.add_field(name="\u200b" if i_chunk else "Role — Members",
                        value=chunk, inline=False)
        await i.response.send_message(embed=e)

    @roles_group.command(name="create", description="Create a new role")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(name="Role name", color="Hex color e.g. #FF0000")
    async def role_create(self, i: discord.Interaction,
                          name: str, color: str = None):
        col = discord.Color.default()
        if color:
            try:
                col = discord.Color(int(color.strip("#"), 16))
            except ValueError:
                await i.response.send_message(
                    "❌ Invalid color. Use hex e.g. `#FF0000`.", ephemeral=True); return
        role = await i.guild.create_role(name=name, color=col,
                                         reason=f"Created by {i.user}")
        await i.response.send_message(embed=discord.Embed(
            description=f"✅ Created {role.mention}.", color=col))

    @roles_group.command(name="delete", description="Delete a role")
    @app_commands.default_permissions(manage_roles=True)
    async def role_delete(self, i: discord.Interaction, role: discord.Role):
        if role >= i.guild.me.top_role:
            await i.response.send_message(
                "❌ Can't delete a role higher than mine.", ephemeral=True); return
        name = role.name
        await role.delete(reason=f"Deleted by {i.user}")
        await i.response.send_message(embed=discord.Embed(
            description=f"🗑️ Deleted role **{name}**.", color=0xFF4444))

    @roles_group.command(name="color", description="Change a role's color")
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.describe(color="Hex color e.g. #FF0000")
    async def role_color(self, i: discord.Interaction,
                         role: discord.Role, color: str):
        if role >= i.guild.me.top_role:
            await i.response.send_message(
                "❌ Can't edit a role higher than mine.", ephemeral=True); return
        try:
            col = discord.Color(int(color.strip("#"), 16))
        except ValueError:
            await i.response.send_message(
                "❌ Invalid hex. Use e.g. `#FF0000`.", ephemeral=True); return
        await role.edit(color=col)
        await i.response.send_message(embed=discord.Embed(
            description=f"✅ {role.mention} color → `{color}`", color=col))

    # ── Prefix parity ─────────────────────────────────────────
    @commands.command(name="roleadd")
    @commands.has_permissions(manage_roles=True)
    async def prefix_role_add(self, ctx: commands.Context,
                               member: discord.Member, role: discord.Role):
        if role in member.roles:
            await ctx.reply(f"❌ Already has {role.mention}."); return
        await member.add_roles(role, reason=f"By {ctx.author}")
        await ctx.send(embed=discord.Embed(
            description=f"✅ Added {role.mention} to {member.mention}.", color=0x57F287))

    @commands.command(name="roleremove")
    @commands.has_permissions(manage_roles=True)
    async def prefix_role_remove(self, ctx: commands.Context,
                                  member: discord.Member, role: discord.Role):
        if role not in member.roles:
            await ctx.reply(f"❌ Doesn't have {role.mention}."); return
        await member.remove_roles(role, reason=f"By {ctx.author}")
        await ctx.send(embed=discord.Embed(
            description=f"✅ Removed {role.mention} from {member.mention}.", color=0x57F287))

    @commands.command(name="roleinfo")
    async def prefix_role_info(self, ctx: commands.Context, role: discord.Role):
        e = discord.Embed(title=f"🎭 {role.name}", color=role.color)
        e.add_field(name="Members",  value=str(len(role.members)))
        e.add_field(name="Color",    value=str(role.color))
        e.add_field(name="Position", value=str(role.position))
        await ctx.send(embed=e)

    @commands.command(name="rolelist")
    async def prefix_role_list(self, ctx: commands.Context):
        roles = sorted(ctx.guild.roles[1:], key=lambda r: r.position, reverse=True)
        lines = [f"{r.mention} `{len(r.members)}`" for r in roles[:20]]
        e = discord.Embed(title=f"🎭 Roles ({len(roles)})",
                          description="\n".join(lines), color=0x5865F2)
        if len(roles) > 20:
            e.set_footer(text=f"+{len(roles)-20} more")
        await ctx.send(embed=e)

async def setup(bot):
    await bot.add_cog(Roles(bot))
