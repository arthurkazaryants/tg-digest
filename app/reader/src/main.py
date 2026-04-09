"""
Reader — подключается к Telegram через Telethon,
читает новые посты из каналов (config/config.yml) и сохраняет в PostgreSQL.
Работает в режиме непрерывного polling с настраиваемым интервалом опроса.
"""

import asyncio
import logging
import os
import signal
import time
from pathlib import Path

import asyncpg
import yaml
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.types import PeerChannel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("reader")

# ── Конфигурация ─────────────────────────────────────────
CONFIG_PATH = os.getenv("CONFIG", "/app/config/config.yml")

# Database pool configuration (now configurable)
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "5"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "20"))

# Validate pool configuration
if DB_POOL_MIN_SIZE > DB_POOL_MAX_SIZE:
    raise ValueError(f"DB_POOL_MIN_SIZE ({DB_POOL_MIN_SIZE}) cannot be greater than DB_POOL_MAX_SIZE ({DB_POOL_MAX_SIZE})")

# Constants for filtering and database operations
DEFAULT_POLL_INTERVAL = 600  # seconds = 10 minutes
DEFAULT_BACKFILL_DAYS = 14
DEFAULT_FETCH_LIMIT = 50  # Default limit for fetching messages per channel

DEBUG_MODE = os.getenv("DEBUG", "false").lower() == "true"

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
    """Загружает весь конфиг."""
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


def load_reader_config(path: str = None) -> tuple[dict, list[dict], dict]:
    """Загружает конфигурацию Reader и каналы.
    
    Возвращает:
        (reader_config, channels, tag_filters)
    """
    config = load_config(path)
    
    # Reader конфиг
    reader_config = config.get("reader", {})
    if not reader_config:
        raise ValueError("reader section not found in config")
    
    channels = config.get("channels", [])
    if not channels:
        raise ValueError("No channels found in config")
    
    tag_filters = config.get("tag_filters", {})
    if not tag_filters:
        raise ValueError("tag_filters not found in config")
    
    logger.debug("Loaded %d channels and tag_filters for tags: %s", len(channels), list(tag_filters.keys()))
    return reader_config, channels, tag_filters


def validate_config(config: dict) -> None:
    """Валидирует структуру конфига на наличие required fields.
    
    Raises:
        ValueError: если конфиг некорректен
    """
    # Reader section
    reader = config.get("reader")
    if not reader:
        raise ValueError("Missing required section: reader")
    
    if "poll_interval_sec" not in reader:
        raise ValueError("Missing required field: reader.poll_interval_sec (e.g., 600 for 10 minutes)")
    
    # Channels section
    channels = config.get("channels")
    if not channels or not isinstance(channels, list):
        raise ValueError("Missing required section: channels (must be a non-empty list)")
    
    for i, ch in enumerate(channels):
        if not ch.get("username"):
            raise ValueError(f"Channel #{i}: missing required field 'username'")
        if not ch.get("tags"):
            raise ValueError(f"Channel '{ch.get('username')}': missing required field 'tags'")
    
    # Tag filters section
    tag_filters = config.get("tag_filters")
    if not tag_filters or not isinstance(tag_filters, dict):
        raise ValueError("Missing required section: tag_filters")
    
    # Validate each tag filter
    for tag_name, tag_filter in tag_filters.items():
        if not isinstance(tag_filter, dict):
            raise ValueError(f"tag_filters.{tag_name}: must be a dictionary")
        
        if "include_keywords" not in tag_filter:
            raise ValueError(f"tag_filters.{tag_name}: missing required field 'include_keywords'")
    
    # Publisher section (if present, should be valid)
    publisher = config.get("publisher")
    if publisher:
        if not isinstance(publisher, dict):
            raise ValueError("publisher: must be a dictionary")
        if "sources" in publisher and not isinstance(publisher["sources"], dict):
            raise ValueError("publisher.sources: must be a dictionary")
    
    logger.debug("Config validation passed")


def text_to_lower(text: str) -> str:
    """Приводит текст к нижнему регистру для сравнения."""
    return text.lower() if text else ""


def matches_keywords(text: str, keywords: list[str], match_all: bool = False) -> bool:
    """Проверяет, соответствует ли текст ключевым словам.
    
    match_all=True: все ключевые слова должны быть в тексте
    match_all=False: хотя бы одно ключевое слово должно быть
    """
    if not keywords:
        return True
    text_lower = text_to_lower(text)
    matches = [kw.lower() in text_lower for kw in keywords]
    return all(matches) if match_all else any(matches)


def apply_tag_filters(text: str, tag_filter: dict) -> bool:
    """Применяет фильтры для конкретного тега к тексту сообщения.
    
    Args:
        text: текст сообщения
        tag_filter: словарь фильтра для тега {include_keywords, exclude_keywords, ...}
    
    Returns:
        True если сообщение проходит все фильтры тега
    """
    if not tag_filter:
        raise ValueError("Tag filter is empty")
    
    include_keywords = tag_filter.get("include_keywords", [])
    
    # include_keywords ОБЯЗАТЕЛЕН
    if not include_keywords:
        raise ValueError("include_keywords is required in tag filter. Use ['*'] to include all messages.")
    
    # Проверяем специальный символ "все сообщения"
    if include_keywords != ["*"]:
        # Должно быть хотя бы одно ключевое слово включения
        if not matches_keywords(text, include_keywords, match_all=False):
            return False
    
    # Не должно быть ключевых слов исключения
    exclude_keywords = tag_filter.get("exclude_keywords", [])
    if exclude_keywords and matches_keywords(text, exclude_keywords, match_all=False):
        return False
    
    # Проверяем seniority (если указан в фильтре, требуем совпадение)
    seniority = tag_filter.get("seniority", [])
    if seniority and not matches_keywords(text, seniority, match_all=False):
        logger.debug("Seniority filter failed: no match in text")
        return False
    
    # Проверяем location (если указан в фильтре, требуем совпадение)
    location_prefs = tag_filter.get("location_preferences", [])
    if location_prefs and not matches_keywords(text, location_prefs, match_all=False):
        logger.debug("Location filter failed: no match in text")
        return False
    
    return True


def should_save_post(text: str, channel_tags: list[str], tag_filters: dict) -> bool:
    """Определяет, нужно ли сохранять сообщение в БД на основе его тегов.
    
    Сообщение сохраняется если оно проходит фильтры для хотя бы одного тега канала.
    
    Args:
        text: текст сообщения
        channel_tags: теги канала (например ["jobs"], ["jobs", "news"])
        tag_filters: загруженные фильтры {"jobs": {...}, "news": {...}, ...}
    
    Returns:
        True если сообщение проходит фильтры хотя бы одного из тегов
    """
    if not text or not channel_tags:
        return False
    
    # Проверяем каждый тег канала
    for tag in channel_tags:
        if tag in tag_filters:
            try:
                if apply_tag_filters(text, tag_filters[tag]):
                    logger.debug("Post matches tag filter: %s", tag)
                    return True  # Если фильтр тега пройден, сохраняем
            except ValueError as e:
                logger.warning("Error applying filter for tag %s: %s", tag, e)
                continue
    
    # Если ни один тег не пройден
    return False


async def get_db_pool() -> asyncpg.Pool:
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


async def init_db(pool: asyncpg.Pool):
    async with pool.acquire() as conn:
        # Создание таблицы
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS raw_posts (
                id           BIGSERIAL PRIMARY KEY,
                channel      TEXT NOT NULL,
                message_id   BIGINT NOT NULL,
                posted_at    TIMESTAMPTZ,
                text         TEXT,
                views        INT DEFAULT 0,
                published    BOOLEAN DEFAULT false,
                fetched_at   TIMESTAMPTZ DEFAULT now(),
                UNIQUE (channel, message_id)
            );
        """)
        
        # Создание индексов для быстрого поиска
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_channel_posted_at 
            ON raw_posts(channel, posted_at DESC);
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_message_id 
            ON raw_posts(message_id);
        """)
        
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_published
            ON raw_posts(published);
        """)
        
        logger.debug("Database initialized successfully")


async def fetch_channel(client: TelegramClient, pool: asyncpg.Pool, channel: dict, tag_filters: dict):
    """Загружает и фильтрует сообщения из канала (только новые после последней синхронизации)."""
    start_time = time.time()
    channel_name = str(channel["username"])  # Ensure it's a string for SQL queries
    
    # Пытаемся получить сущность канала с обработкой ошибок
    try:
        entity = await client.get_entity(channel_name)
    except Exception as e:
        # Fallback для приватных каналов: поищи в диалогах
        logger.debug("Failed to get entity directly, searching in dialogs for %s", channel_name)
        try:
            entity = None
            channel_id_int = None
            
            # Попытка преобразования в int для сравнения
            try:
                channel_id_int = int(channel_name)
            except ValueError:
                pass
            
            # Поиск в диалогах пользователя
            async for dialog in client.iter_dialogs():
                dialog_id = dialog.entity.id
                # Сравниваем по ID (обработка как приватного канала)
                if channel_id_int and dialog_id == channel_id_int:
                    entity = dialog.entity
                    break
                # Для приватных супергрупп ID может быть отрицательным
                if channel_id_int and dialog_id == -channel_id_int:
                    entity = dialog.entity
                    break
                # Сравниваем как строки
                if str(dialog_id) == channel_name or str(dialog_id).lstrip('-') == channel_name.lstrip('-'):
                    entity = dialog.entity
                    break
            
            if not entity:
                raise ValueError(f"Channel {channel_name} not found in user dialogs")
        except Exception as e2:
            logger.error("Failed to get entity for channel %s: %s. Skipping this channel.", channel_name, e2)
            return
    
    limit = channel.get("limit", 50)
    tags = channel.get("tags", [])
    
    # Получаем последний message_id для этого канала
    async with pool.acquire() as conn:
        last_message_id = await conn.fetchval(
            "SELECT MAX(message_id) FROM raw_posts WHERE channel = $1",
            channel_name
        )
    last_message_id = last_message_id or 0
    
    logger.info("Fetching messages from %s (last_id=%d, limit=%d, tags=%s)", 
                channel_name, last_message_id, limit, tags)

    messages_to_insert = []
    filtered_count = 0
    
    # Загружаем только сообщения с ID > last_message_id
    async for message in client.iter_messages(entity, limit=limit, min_id=last_message_id):
        if not message.text:
            continue
        
        # Применяем фильтры на основе тегов канала
        if not should_save_post(message.text, tags, tag_filters):
            filtered_count += 1
            logger.debug("Filtered message from %s (ID: %d)", channel_name, message.id)
            continue
        
        messages_to_insert.append((
            channel["username"],
            message.id,
            message.date,
            message.text,
            message.views or 0,
        ))

    # Батч-вставка для эффективности
    if messages_to_insert:
        async with pool.acquire() as conn:
            await conn.executemany(
                """
                INSERT INTO raw_posts (channel, message_id, posted_at, text, views)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (channel, message_id) DO NOTHING
                """,
                messages_to_insert,
            )
        elapsed = time.time() - start_time
        logger.info("✓ Channel %s: inserted %d messages (filtered out %d) in %.2f seconds", 
                   channel_name, len(messages_to_insert), filtered_count, elapsed)
    else:
        elapsed = time.time() - start_time
        logger.info("✓ Channel %s: no new messages (filtered out %d) in %.2f seconds", 
                   channel_name, filtered_count, elapsed)


async def fetch_all_channels(client: TelegramClient, pool: asyncpg.Pool, reader_config: dict, channels: list, tag_filters: dict):
    """Выполняет один цикл опроса всех каналов с применением фильтров по тегам.
    
    Args:
        client: TelegramClient
        pool: asyncpg connection pool
        reader_config: reader configuration dict
        channels: list of channel configurations
        tag_filters: dict of tag filter configurations
    """
    logger.info("Starting fetch cycle for %d channels", len(channels))
    logger.info("Loaded tag_filters for tags: %s", list(tag_filters.keys()))
    
    for ch in channels:
        try:
            await fetch_channel(client, pool, ch, tag_filters)
        except Exception:
            logger.exception("Error fetching %s", ch.get("username"))
    
    logger.info("Fetch cycle completed")


async def main():
    logger.info("Starting reader service...")
    
    # Проверка всех необходимых secrets перед началом работы
    logger.info("Verifying secrets...")
    required_secrets = ["tg_api_id", "tg_api_hash", "tg_reader_session", "pg_password"]
    for secret_name in required_secrets:
        try:
            read_secret(secret_name)
            logger.info("✓ Secret %s found", secret_name)
        except (FileNotFoundError, ValueError) as e:
            logger.critical("Missing or invalid secret: %s. Cannot start. Error: %s", secret_name, e)
            raise SystemExit(1) from e
    
    logger.info("All secrets verified successfully")
    
    # Загрузка конфига (проверка синтаксиса и валидность структуры)
    logger.info("Loading configuration...")
    try:
        config = load_config()
        validate_config(config)  # Validate structure
        reader_config, channels, tag_filters = load_reader_config()
        logger.info("✓ Config loaded: %d channels with tags %s", len(channels), list(tag_filters.keys()))
        poll_interval = reader_config.get('poll_interval_sec', DEFAULT_POLL_INTERVAL)
        logger.info("✓ Reader config: poll_interval=%ds", poll_interval)
    except Exception as e:
        logger.critical("Failed to load config: %s", e)
        raise SystemExit(1) from e
    
    # Подключение к Telegram
    api_id = int(read_secret("tg_api_id"))
    api_hash = read_secret("tg_api_hash")
    session_str = read_secret("tg_reader_session")

    # Создаём клиент с сохранённой сессией (если существует)
    if not session_str or not session_str.strip():
        logger.warning("Empty session string - will require new Telegram authentication")
        session = StringSession()
    else:
        session = StringSession(session_str)
    client = TelegramClient(session, api_id, api_hash)
    
    logger.info("Connecting to Telegram...")
    await client.start()
    logger.info("✓ Successfully connected to Telegram")
    
    # Debug: Log all available dialogs with their IDs
    logger.info("=== Available channels in user dialogs ===")
    dialog_count = 0
    async for dialog in client.iter_dialogs(limit=200):
        entity = dialog.entity
        if hasattr(entity, 'title'):
            dialog_count += 1
            logger.info(f"  ID: {entity.id:15} | Alt: -100{abs(entity.id)%1000000000:9} | Title: {entity.title}")
    logger.info(f"Total dialogs found: {dialog_count}")
    logger.info("=== End of channels list ===")

    pool = await get_db_pool()
    await init_db(pool)

    shutdown_event = asyncio.Event()
    
    def handle_signal(signum, frame):
        logger.info("Received signal %d, shutting down...", signum)
        shutdown_event.set()
    
    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        # Perform initial fetch immediately
        logger.info("Performing initial fetch...")
        await fetch_all_channels(client, pool, reader_config, channels, tag_filters)
        
        # Continuous polling loop
        logger.info("Starting continuous polling loop (interval: %ds)", poll_interval)
        while not shutdown_event.is_set():
            try:
                # Sleep until next poll or shutdown signal
                await asyncio.wait_for(shutdown_event.wait(), timeout=poll_interval)
                # If we get here, shutdown was requested
                logger.info("Shutdown signal received")
                break
            except asyncio.TimeoutError:
                # Timeout means it's time to fetch again
                try:
                    await fetch_all_channels(client, pool, reader_config, channels, tag_filters)
                except Exception:
                    logger.exception("Error in fetch cycle (will retry)")
                    # Continue polling even on error
            
    finally:
        logger.info("Closing database pool...")
        await pool.close()
        
        logger.info("Disconnecting from Telegram...")
        await client.disconnect()
        
        logger.info("Reader finished")


if __name__ == "__main__":
    asyncio.run(main())
