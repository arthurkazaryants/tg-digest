"""
Publisher — собирает неопубликованные digest_items,
формирует итоговый пост и отправляет в Telegram-канал.
"""

import asyncio
import logging
import os
from pathlib import Path

import asyncpg
from telethon import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("publisher")

CATEGORY_EMOJI = {
    "jobs": "💼",
    "news": "📰",
    "learning": "📚",
}


def read_secret(name: str) -> str:
    secret_path = Path(f"/run/secrets/{name}")
    if secret_path.exists():
        return secret_path.read_text().strip()
    raise FileNotFoundError(f"Secret {name} not found")


async def get_db_pool() -> asyncpg.Pool:
    password = read_secret("pg_password")
    return await asyncpg.create_pool(
        host="postgres",
        port=5432,
        user=os.getenv("POSTGRES_USER", "tg_digest"),
        password=password,
        database=os.getenv("POSTGRES_DB", "tg_digest"),
    )


async def build_digest(pool: asyncpg.Pool) -> str | None:
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT id, category, summary
            FROM digest_items
            WHERE published = FALSE
            ORDER BY category, created_at
        """)

    if not rows:
        logger.info("No unpublished items — nothing to send")
        return None

    sections: dict[str, list[str]] = {}
    ids: list[int] = []
    for row in rows:
        cat = row["category"]
        emoji = CATEGORY_EMOJI.get(cat, "•")
        sections.setdefault(cat, []).append(f"  {emoji} {row['summary']}")
        ids.append(row["id"])

    lines = ["📋 **Дайджест дня**\n"]
    for cat in ["news", "jobs", "learning"]:
        if cat in sections:
            lines.append(f"\n**{cat.upper()}**")
            lines.extend(sections[cat])

    # Помечаем как опубликованные
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE digest_items SET published = TRUE WHERE id = ANY($1::bigint[])",
            ids,
        )

    return "\n".join(lines)


async def main():
    api_id = int(read_secret("tg_api_id"))
    api_hash = read_secret("tg_api_hash")
    channel = os.getenv("TG_PUBLISHER_CHANNEL", "@my_digest_channel")

    client = TelegramClient("publisher_session", api_id, api_hash)
    await client.start()

    pool = await get_db_pool()
    digest_text = await build_digest(pool)

    if digest_text:
        await client.send_message(channel, digest_text, parse_mode="md")
        logger.info("Digest published to %s", channel)

    await pool.close()
    await client.disconnect()
    logger.info("Publisher finished")


if __name__ == "__main__":
    asyncio.run(main())
