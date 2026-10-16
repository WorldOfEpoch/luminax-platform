import asyncio
import json
import os
import sys

import discord

from bot import bot, ensure_discord_admin_tables, provision_papr_guild


TARGET_GUILD_ID = int(os.getenv("PAPR_GUILD_ID", "0") or "0")
TARGET_GUILD_NAME = (os.getenv("PAPR_GUILD_NAME", "") or "").strip()
DISCORD_TOKEN = (os.getenv("DISCORD_TOKEN", "") or "").strip()


async def main() -> int:
    if os.getenv("DISCORD_PROVISIONING_ENABLED", "false").lower() != "true" or TARGET_GUILD_ID <= 0:
        print(json.dumps({"status": "disabled", "detail": "Explicit DISCORD_PROVISIONING_ENABLED=true and PAPR_GUILD_ID are required"}))
        return 1
    if not DISCORD_TOKEN:
        print(json.dumps({"status": "error", "detail": "DISCORD_TOKEN is not set"}, indent=2))
        return 1

    result: dict[str, object] = {}

    @bot.event
    async def on_ready():
        nonlocal result
        try:
            ensure_discord_admin_tables()
            guild = bot.get_guild(TARGET_GUILD_ID) if TARGET_GUILD_ID else None
            if guild is None and TARGET_GUILD_NAME:
                guild = discord.utils.get(bot.guilds, name=TARGET_GUILD_NAME)
            if guild is None:
                result = {
                    "status": "error",
                    "detail": "target guild not found",
                    "guild_id": TARGET_GUILD_ID or None,
                    "guild_name": TARGET_GUILD_NAME or None,
                    "available_guilds": [{"id": g.id, "name": g.name} for g in bot.guilds],
                }
            else:
                result = await provision_papr_guild(guild, requested_by=0)
        except Exception as e:
            result = {"status": "error", "detail": str(e)}
        finally:
            print(json.dumps(result, indent=2))
            await bot.close()

    await bot.start(DISCORD_TOKEN)
    return 0 if result.get("status") == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
