"""Ping cog — hybrid command that reports WebSocket latency."""

from __future__ import annotations

from discord.ext import commands


class PingCog(commands.Cog):
    """Simple latency check cog."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(name="ping", description="Check bot latency")
    async def ping(self, ctx: commands.Context) -> None:
        """Respond with the bot's WebSocket latency in milliseconds."""
        latency_ms = round(self.bot.latency * 1000)
        await ctx.send(f"Pong! {latency_ms}ms")


async def setup(bot: commands.Bot) -> None:
    """Load the PingCog into the bot (called by load_extension)."""
    await bot.add_cog(PingCog(bot))
