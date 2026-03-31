"""
Publisher — периодически читает неопубликованные посты из БД 
и отправляет их в Telegram-канал.

Две стратегии публикации:
1. Плановая: по расписанию (из config) отправляет до N сообщений
2. Срочная: если очередь переполнена (> queue_threshold), публикует сразу

Архитектура:
  raw_posts[published=false] → format → send to Telegram → mark published=true
"""

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

import asyncpg
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telethon import TelegramClient
from telethon.sessions import StringSession

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("publisher")

# ── Конфигурация ─────────────────────────────────────────
CONFIG_PATH = os.getenv("CONFIG", "/app/config/config.yml")

# Database pool configuration
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "5"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "20"))

# Validate pool configuration
if DB_POOL_MIN_SIZE > DB_POOL_MAX_SIZE:
    raise ValueError(f"DB_POOL_MIN_SIZE ({DB_POOL_MIN_SIZE}) cannot be greater than DB_POOL_MAX_SIZE ({DB_POOL_MAX_SIZE})")

DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

# Constants for config defaults
DEFAULT_SCHEDULE = "0 */1 * * *"  # Every hour
DEFAULT_QUEUE_CHECK_INTERVAL = 300  # 5 minutes
DEFAULT_QUEUE_THRESHOLD = 20  # posts
DEFAULT_BATCH_LIMIT = 10  # messages per batch

if DEBUG_MODE:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)


def read_secret(name: str) -> str:
    """Читает Docker-secret из /run/secrets/<name>."""
    secret_path = Path(f"/run/secrets/{name}")
    try:
        if secret_path.exists():
            content = secret_path.read_text().strip()
            if not content:
                raise ValueError(f"Secret {name} is empty")
            logger.debug("Loaded secret: %s", name)
            return content
        raise FileNotFoundError(f"Secret {name} not found at {secret_path}")
    except Exception as e:
        logger.error("Failed to load secret %s: %s", name, e)
        raise


def load_config(path: str = None) -> dict:
    """Загружает конфиг из YAML файла."""
    if path is None:
        path = CONFIG_PATH
    
    logger.info("Loading config from %s", path)
    try:
        with open(path) as f:
            config = yaml.safe_load(f)
        if not config:
            raise ValueError("Config file is empty")
        logger.debug("Config loaded successfully")
        return config
    except FileNotFoundError:
        raise FileNotFoundError(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise ValueError(f"Invalid YAML in config: {e}")


def load_publisher_config(path: str = None) -> dict:
    """Загружает конфигурацию Publisher из конфига.
    
    Returns:
        {
            'schedule': '0 */1 * * *',
            'queue_check_interval': 300,
            'queue_threshold': 20,
            'sources': {
                'jobs': {
                    'enabled': true,
                    'target_channel': '@my_digest_channel',
                    'batch_limit': 10
                }
            }
        }
    """
    config = load_config(path)
    validate_config(config)  # Validate publisher section exists
    
    publisher_config = config.get("publisher", {})
    if not publisher_config:
        raise ValueError("publisher section not found in config")
    
    # Валидируем required fields
    if "sources" not in publisher_config:
        raise ValueError("publisher.sources not found in config")
    
    logger.info("Publisher config loaded: schedule=%s, sources=%s", 
                publisher_config.get('schedule'), 
                list(publisher_config.get('sources', {}).keys()))
    
    return publisher_config


def validate_config(config: dict) -> None:
    """Валидирует что publisher секция присутствует в конфиге.
    
    Raises:
        ValueError: если publisher section отсутствует
    """
    if not isinstance(config, dict):
        raise ValueError("Config must be a dictionary")
    
    # Check publisher section (if present, should be valid)
    publisher = config.get("publisher")
    if publisher:
        if not isinstance(publisher, dict):
            raise ValueError("publisher: must be a dictionary")
        
        if "sources" in publisher:
            if not isinstance(publisher["sources"], dict):
                raise ValueError("publisher.sources: must be a dictionary")
            
            # Validate each source
            for source_name, source_config in publisher["sources"].items():
                if not isinstance(source_config, dict):
                    raise ValueError(f"publisher.sources.{source_name}: must be a dictionary")
                
                # If enabled is true, target_channel and batch_limit are required
                if source_config.get("enabled", False):
                    if "target_channel" not in source_config:
                        raise ValueError(f"publisher.sources.{source_name}: missing 'target_channel' for enabled source")
    
    logger.debug("Config validation passed")


async def get_db_pool() -> asyncpg.Pool:
    """Создаёт пул подключений к PostgreSQL."""
    password = read_secret("pg_password")
    
    postgres_user = os.getenv("POSTGRES_USER", "tg_digest")
    postgres_db = os.getenv("POSTGRES_DB", "tg_digest")
    
    logger.info("Creating database connection pool for user=%s, db=%s (pool_size=%d..%d)", 
                postgres_user, postgres_db, DB_POOL_MIN_SIZE, DB_POOL_MAX_SIZE)
    return await asyncpg.create_pool(
        host="postgres",
        port=5432,
        user=postgres_user,
        password=password,
        database=postgres_db,
        min_size=DB_POOL_MIN_SIZE,
        max_size=DB_POOL_MAX_SIZE,
    )


def format_post(post: dict) -> str:
    """Форматирует пост для отправки в Telegram.
    
    Args:
        post: словарь с полями {channel, text, posted_at, views}
    
    Returns:
        Отформатированное сообщение (макс 4096 символов по лимиту Telegram)
    """
    channel = post["channel"]
    text = post["text"]
    views = post.get("views", 0)
    
    # Telegram лимит 4096 символов на сообщение
    # Header/footer примерно: "💼 " + "\n\n📌 Source: @{channel}\n👁 Views: {views}" = ~80-100 символов
    # Оставляем 150 символов margin на всякий случай
    TELEGRAM_MESSAGE_LIMIT = 4096
    HEADER_FOOTER_SIZE = 150  # Conservative estimate for emoji, formatting, channel name, view count
    MAX_TEXT_LENGTH = TELEGRAM_MESSAGE_LIMIT - HEADER_FOOTER_SIZE
    
    if len(text) > MAX_TEXT_LENGTH:
        text = text[:MAX_TEXT_LENGTH] + "…"
    
    message = f"""💼 {text}

📌 Source: @{channel}
👁 Views: {views}"""
    
    return message


async def get_unpublished_count(pool: asyncpg.Pool) -> int:
    """Получает количество неопубликованных постов."""
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM raw_posts WHERE published = false",
        )
    return count or 0


async def fetch_unpublished_posts(pool: asyncpg.Pool, limit: int) -> list[dict]:
    """Получает неопубликованные посты из БД.
    
    Args:
        pool: пул подключений
        limit: макс количество постов
    
    Returns:
        Список постов
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, channel, message_id, text, posted_at, views
            FROM raw_posts
            WHERE published = false
            ORDER BY posted_at DESC
            LIMIT $1
            """,
            limit,
        )
    return [dict(row) for row in rows]


async def mark_as_published(pool: asyncpg.Pool, post_id: int):
    """Отмечает пост как опубликованный."""
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE raw_posts SET published = true WHERE id = $1",
            post_id,
        )


def parse_cron(cron_str: str) -> dict:
    """Парсит и валидирует cron строку в параметры для APScheduler.
    
    Формат: "minute hour day month day_of_week"
    Пример: "0 */1 * * *" → каждый час в 0 минут
    
    Raises:
        ValueError: если cron формат неправильный или значения невалидны
    """
    parts = cron_str.split()
    if len(parts) != 5:
        raise ValueError(f"Invalid cron format: {cron_str}. Expected 'minute hour day month day_of_week'")
    
    minute, hour, day, month, day_of_week = parts
    
    # Базовая валидация (не все случаи, но хотя бы основные)
    for part, name, min_val, max_val in [(minute, 'minute', 0, 59), (hour, 'hour', 0, 23)]:
        if part != '*' and '/' not in part and '-' not in part:
            try:
                val = int(part)
                if not (min_val <= val <= max_val):
                    raise ValueError(f"Invalid {name}: {val} not in range {min_val}-{max_val}")
            except ValueError as e:
                raise ValueError(f"Invalid {name} in cron: {part}. Error: {e}")
    
    return {
        'minute': minute,
        'hour': hour,
        'day': day,
        'month': month,
        'day_of_week': day_of_week,
    }


async def publish_batch(client: TelegramClient, pool: asyncpg.Pool, config: dict):
    """Основная логика публикации: читаем → форматируем → отправляем → отмечаем.
    
    Отправляет все неопубликованные посты для первого enabled источника.
    """
    start_time = time.time()
    
    # Получаем первый enabled источник
    sources = config.get('sources', {})
    enabled_sources = {k: v for k, v in sources.items() if v.get('enabled', False)}
    
    if not enabled_sources:
        logger.warning("No enabled sources in config")
        return
    
    # Берём первый enabled источник (можно расширить на multiple источниках в будущем)
    source_tag = next(iter(enabled_sources.keys()))
    source_config = enabled_sources[source_tag]
    batch_limit = source_config.get('batch_limit', DEFAULT_BATCH_LIMIT)
    target_channel = source_config.get('target_channel')
    
    # Получаем список неопубликованных постов
    posts = await fetch_unpublished_posts(pool, batch_limit)
    
    if not posts:
        logger.info("✓ No unpublished posts to send")
        return
    
    logger.info(f"Publishing {len(posts)} posts to {target_channel}...")
    
    sent_count = 0
    failed_count = 0
    
    for post in posts:
        try:
            # Форматируем и отправляем
            message = format_post(post)
            await client.send_message(target_channel, message, parse_mode='markdown')
            
            # Отмечаем как опубликованное
            await mark_as_published(pool, post["id"])
            sent_count += 1
            
            logger.debug(f"✓ Post {post['id']} from @{post['channel']} published")
            
        except Exception as e:
            failed_count += 1
            logger.error(f"Failed to publish post {post['id']}: {e}")
            # Продолжаем со следующего поста
    
    elapsed = time.time() - start_time
    logger.info(f"✓ Publishing completed: {sent_count} sent, {failed_count} failed in {elapsed:.2f}s")


async def check_queue_and_publish_if_overflow(client: TelegramClient, pool: asyncpg.Pool, config: dict):
    """Проверяет размер очереди. Если > queue_threshold, публикует сразу.
    
    Это предотвращает накопление постов и спам одного большого выброса.
    """
    queue_threshold = config.get('queue_threshold', 20)
    count = await get_unpublished_count(pool)
    
    if count >= queue_threshold:
        logger.warning(f"Queue overflow detected: {count} unpublished posts (threshold={queue_threshold}). Publishing early!")
        await publish_batch(client, pool, config)
    else:
        logger.debug(f"Queue status: {count}/{queue_threshold} posts")


async def main():
    logger.info("Starting publisher service...")
    
    # Загрузка конфига (проверка синтаксиса и валидность структуры)
    logger.info("Loading configuration...")
    try:
        publisher_config = load_publisher_config()
        logger.info("✓ Config loaded successfully")
    except Exception as e:
        logger.critical(f"Failed to load config: {e}")
        raise SystemExit(1) from e
    
    # Проверка необходимых secrets
    logger.info("Verifying secrets...")
    required_secrets = ["tg_api_id", "tg_api_hash", "tg_publisher_session", "pg_password"]
    for secret_name in required_secrets:
        try:
            read_secret(secret_name)
            logger.info(f"✓ Secret {secret_name} found")
        except (FileNotFoundError, ValueError) as e:
            logger.critical(f"Missing or invalid secret: {secret_name}. Cannot start. Error: {e}")
            raise SystemExit(1) from e
    
    logger.info("All secrets verified successfully")
    
    # Подключение к Telegram
    api_id = int(read_secret("tg_api_id"))
    api_hash = read_secret("tg_api_hash")
    session_str = read_secret("tg_publisher_session")
    
    # Создаём клиент с сохранённой сессией
    if not session_str or not session_str.strip():
        logger.warning("Empty session string - will require new Telegram authentication")
        session = StringSession()
    else:
        session = StringSession(session_str)
    
    client = TelegramClient(session, api_id, api_hash)
    
    logger.info("Connecting to Telegram...")
    await client.start()
    logger.info("✓ Successfully connected to Telegram")
    
    pool = await get_db_pool()
    
    try:
        # Параметры из конфига
        schedule = publisher_config.get('schedule', '0 */1 * * *')
        queue_check_interval = publisher_config.get('queue_check_interval', 300)
        sources = publisher_config.get('sources', {})
        enabled_sources = {k: v for k, v in sources.items() if v.get('enabled', False)}
        
        logger.info(f"Scheduler configured:")
        logger.info(f"  - Schedule: {schedule} (from config)")
        logger.info(f"  - Queue monitor: every {queue_check_interval}s")
        logger.info(f"  - Enabled sources: {list(enabled_sources.keys())}")
        
        scheduler = AsyncIOScheduler()
        
        # Job 1: Плановая публикация по расписанию из конфига
        scheduler.add_job(
            publish_batch,
            "cron",
            **parse_cron(schedule),
            args=(client, pool, publisher_config),
            id="scheduled_publish",
        )
        
        # Job 2: Мониторинг очереди
        scheduler.add_job(
            check_queue_and_publish_if_overflow,
            "interval",
            seconds=queue_check_interval,
            args=(client, pool, publisher_config),
            id="queue_monitor",
        )
        
        scheduler.start()
        
        # Выполнить первую проверку сразу
        await check_queue_and_publish_if_overflow(client, pool, publisher_config)
        await publish_batch(client, pool, publisher_config)
        
        # Ждём сигнала завершения
        shutdown_event = asyncio.Event()
        
        def handle_signal(signum, frame):
            logger.info(f"Received signal {signum}, shutting down...")
            shutdown_event.set()
        
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        
        try:
            await shutdown_event.wait()
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt received")
        
        logger.info("Stopping scheduler...")
        scheduler.shutdown(wait=True)
    
    finally:
        logger.info("Closing database pool...")
        await pool.close()
        
        logger.info("Disconnecting from Telegram...")
        await client.disconnect()
        
        logger.info("Publisher finished")


if __name__ == "__main__":
    asyncio.run(main())
