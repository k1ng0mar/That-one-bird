# cogs/utils_cog.py — registers utils as a loadable extension (no commands)
from discord.ext import commands

async def setup(bot):
    pass  # utils.py is a plain module, no cog needed — this just prevents load errors
