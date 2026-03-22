"""Entry point for running the bot: python -m bot."""

from __future__ import annotations

import logging
import sys

from bot.bot import DiscordBot
from bot.config import Config


def main() -> None:
    """Load config, create bot, and run."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    log = logging.getLogger(__name__)

    try:
        config = Config.from_env()
    except ValueError as exc:
        log.error("Configuration error: %s", exc)
        sys.exit(1)

    bot = DiscordBot(config)

    try:
        bot.run(config.discord_bot_token, log_handler=None)
    except Exception as exc:
        log.error("Bot startup failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
