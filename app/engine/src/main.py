"""
Engine — берёт необработанные посты из БД,
классифицирует их по категориям (jobs / news / learning),
генерирует краткое саммари через LLM и сохраняет результат.
"""

import asyncio
import logging
import os
from pathlib import Path

import asyncpg
from openai import AsyncOpenAI

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("engine")

CATEGORIES = ["jobs", "news", "learning"]


def read_secret(name: str) -> str:
    secret_path = Path(f"/run/secrets/{name}")
    if secret_path.exists():
        return secret_path.read_text().strip()
    raise FileNotFoundError(f"Secret {name} not found")


def load_prompt(category: str) -> str:
    path = Path(f"/app/config/prompts/{category}.txt")
    return path.read_text()


async def get_db_pool() -> asyncpg.Pool:
    password = read_secret("pg_password")
    return await asyncpg.create_pool(
        host="postgres",
        port=5432,
        user=os.getenv("POSTGRES_USER", "tg_digest"),
        password=password,
        database=os.getenv("POSTGRES_DB", "tg_digest"),
    )


async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS digest_items (
                id           BIGSERIAL PRIMARY KEY,
                raw_post_id  BIGINT REFERENCES raw_posts(id),
                category     TEXT NOT NULL,
                summary      TEXT NOT NULL,
                created_at   TIMESTAMPTZ DEFAULT now(),
                published    BOOLEAN DEFAULT FALSE
            );
        """)


async def classify_and_summarise(client: AsyncOpenAI, text: str, model: str) -> dict | None:
    """Возвращает {category, summary} или None, если пост нерелевантен."""
    system = (
        "Ты — ассистент, который классифицирует посты из Telegram-каналов. "
        "Определи категорию (jobs, news, learning) и кратко перескажи суть. "
        "Если пост не подходит ни к одной категории, верни null."
    )
    resp = await client.chat.completions.create(
        model=model,
        temperature=float(os.getenv("LLM_TEMPERATURE", "0.3")),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": text},
        ],
        response_format={"type": "json_object"},
    )
    import json
    result = json.loads(resp.choices[0].message.content)
    if result.get("category") in CATEGORIES:
        return result
    return None


async def process_unprocessed(pool: asyncpg.Pool, llm: AsyncOpenAI, model: str):
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT rp.id, rp.text
            FROM raw_posts rp
            LEFT JOIN digest_items di ON di.raw_post_id = rp.id
            WHERE di.id IS NULL
            ORDER BY rp.fetched_at DESC
            LIMIT 200
        """)

    logger.info("Found %d unprocessed posts", len(rows))
    for row in rows:
        try:
            result = await classify_and_summarise(llm, row["text"], model)
            if result:
                async with pool.acquire() as conn:
                    await conn.execute(
                        """
                        INSERT INTO digest_items (raw_post_id, category, summary)
                        VALUES ($1, $2, $3)
                        """,
                        row["id"],
                        result["category"],
                        result["summary"],
                    )
                logger.info("Post %d → %s", row["id"], result["category"])
        except Exception:
            logger.exception("Error processing post %d", row["id"])


async def main():
    api_key = read_secret("llm_api_key")
    model = os.getenv("LLM_MODEL", "gpt-4o-mini")

    llm = AsyncOpenAI(api_key=api_key)
    pool = await get_db_pool()
    await init_db(pool)

    await process_unprocessed(pool, llm, model)

    await pool.close()
    logger.info("Engine finished")


if __name__ == "__main__":
    asyncio.run(main())
