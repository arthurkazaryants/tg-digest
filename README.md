# TG-Digest: Универсальный сборщик контента из Telegram

Двухстадийный pipeline для сбора и публикации информации из Telegram-каналов с интеллектуальной фильтрацией:
- **Reader** ✅ — собирает посты из каналов с фильтрацией
- **Engine** ⏸️ — обрабатывает через LLM (не используется, зарезервировано)
- **Publisher** ✅ — публикует дайджест в Telegram-канал

## 🚀 Требования

- Docker & Docker Compose 3.9+
- Python 3.12+ (для локального запуска)
- PostgreSQL 16 (в контейнере)

### Optional Dependencies

**Engine** (когда будет готово к использованию):
- OpenAI API ключ (для LLM обработки)
- `python-openai >= 1.51.0` (установить отдельно)

В настоящий момент Engine не используется. Когда потребуется интеграция с LLM, обновите конфиг в `config/config.yml`.

## 📋 Подготовка

### 1. Структура secrets

Создай папку `../tg-digest-secrets/` с файлами:

```bash
../tg-digest-secrets/
├── pg_password.txt          # пароль PostgreSQL
├── tg_api_id.txt            # Telegram API ID
├── tg_api_hash.txt          # Telegram API Hash
├── tg_reader_session.txt    # Telegram сессия reader
└── tg_publisher_session.txt # Telegram сессия publisher
```

<details>
<summary><b>Как получить Telegram API credentials</b></summary>

1. Перейди на https://my.telegram.org
2. Phone Number → Enter Your Phone Number
3. Получишь Code в Telegram → введи его
4. Выбери или создай App → скопируй Api ID и Api Hash
5. Сохрани в `tg_api_id.txt` и `tg_api_hash.txt`

Сессию reader/publisher будет создана автоматически при первом запуске.
</details>

### 2. Конфиг каналов

Отредактируй `config/channels.yml`:

```yaml
channels:
  - username: remote_it_jobs      # Telegram-канал без @
    limit: 40                       # Макс. последних постов за опрос
    tags: [jobs]                    # Теги для связи с tag_filters
  
  - username: news_channel
    limit: 50
    tags: [news]
  
  - username: learning_hub
    limit: 30
    tags: [learning]

tag_filters:                        # Универсальный механизм фильтрации
  jobs:                             # Фильтры для тега "jobs"
    include_keywords:               # ОБЯЗАТЕЛЕН! хотя бы одно слово
      - "cto"
      - "devops"
      - "kubernetes"
      - "sre"
      # Или используй ["*"] для включения всех постов (тестирование)
    
    exclude_keywords:               # ОПЦИОНАЛЬНО
      - "junior"
      - "intern"
    
    location_preferences:           # ОПЦИОНАЛЬНО
      - "remote"
      - "москва"
    
    seniority:                      # ОПЦИОНАЛЬНО
      - "lead"
      - "head"
      - "principal"
  
  news:                            # Фильтры для тега "news"
    include_keywords: ["*"]         # Принимай все новости
  
  learning:                         # Фильтры для тега "learning"
    include_keywords:
      - "tutorial"
      - "guide"
      - "course"
      - "learn"
```

### Параметры каналов

| Параметр | Тип | Обязательный | Описание |
|----------|-----|--------------|---------|
| `username` | string | ✓ | Имя Telegram-канала без @ |
| `limit` | integer | ✓ | Макс. последних постов за один опрос (см. ниже) |
| `tags` | list | ✓ | Теги категорий связи с фильтрами |

<details>
<summary><b>Что означает `limit`?</b></summary>

`limit` — количество **последних постов**, которые reader загружает за один опрос:

```yaml
channels:
  - username: remote_it_jobs
    limit: 40              # Загружать последние 40 постов за опрос
```

**Как работает:**

| Этап | Поведение |
|------|-----------|
| **1-й опрос** | Загружает последние 40 постов из канала |
| **2-й опрос (через час)** | Загружает только НОВЫЕ посты (после последнего message_id) |
| **n-й опрос** | Всегда загружает только новые, limit — просто максимум |

**Рекомендации:**

- **40-50** — оптимально для медленных каналов (1-10 постов/час)
- **100** — для активных каналов (50+ постов/час)
- **Если пропускаются посты** — увеличь значение

**Пример: разные скорости активности**

```yaml
channels:
  - username: remote_it_jobs
    limit: 100    # Очень активный канал
    tags: [jobs]
  
  - username: learning_hub
    limit: 40     # Медленный канал, достаточно 40
    tags: [learning]
  
  - username: breaking_news
    limit: 200    # Очень высокая активность
    tags: [news]
```
</details>

### 3. Переменные окружения

`.env`:
```bash
POSTGRES_USER=tg_digest
POSTGRES_DB=tg_digest

# Кронрасписание
READER_CRON="0 */4 * * *"    # каждые 4 часа
ENGINE_CRON="0 8 * * *"      # в 08:00
PUBLISHER_CRON="0 9 * * *"   # в 09:00
```

`docker-compose.yml` переменные:
```yaml
FETCH_MODE: polling              # "once" или "polling"
POLL_INTERVAL_SEC: 3600          # интервал опроса (сек)
BACKFILL_DAYS: 14                # глубина первого опроса
DEBUG: "false"                   # вкл/выкл DEBUG логирование
CHANNELS_CONFIG: /app/config/channels.yml
```

## ▶️ Запуск

### Запуск reader (основной сервис)

```bash
# Построить образ
docker-compose build reader

# Запустить с логами
docker-compose up reader

# Запустить в фоне
docker-compose up -d reader
docker-compose logs -f reader
```

### Запуск с DEBUG логированием

```bash
docker-compose run -e DEBUG=true reader
```

### Проверка БД

```bash
# Подключиться к PostgreSQL
docker-compose exec postgres psql -U tg_digest -d tg_digest

# Посмотреть загруженные посты
SELECT channel, COUNT(*) as count 
FROM raw_posts 
GROUP BY channel;

# Посмотреть последние посты
SELECT channel, message_id, posted_at, text 
FROM raw_posts 
ORDER BY posted_at DESC 
LIMIT 5;
```

### Остановка

```bash
docker-compose down

# Удалить БД (очистить данные)
docker-compose down -v
```

## 🔧 Production развёртывание

### Подготовка сервера (автоматизированная)

**Используй скрипт `setup-production-server.sh` для автоматической подготовки сервера:**

```bash
# 1. На локальной машине скопируй скрипт на сервер
scp setup-production-server.sh root@your-server:/tmp/

# 2. На сервере выполни скрипт (требует sudo)
ssh root@your-server "bash /tmp/setup-production-server.sh"
```

**Скрипт автоматически:**
- ✅ Проверяет требования (OS, привилегии)
- ✅ Устанавливает Docker и Docker Compose
- ✅ Создаёт непривилегированного пользователя `tg-digest` 
- ✅ Добавляет пользователя в группу `docker` (может запускать docker без sudo)
- ✅ Создаёт папки проекта (`/opt/tg-digest`) и secrets (`/opt/tg-digest-secrets`)
- ✅ Устанавливает правильные права доступа (secrets: 700 = только владелец)
- ✅ Создаёт template-файлы для secrets (нужно заполнить реальными значениями)
- ✅ Настраивает firewall (UFW/iptables)
- ✅ Валидирует всю установку

### На сервере (после запуска setup-скрипта)

**Все операции выполняются от пользователя `tg-digest` (не от root!):**

```bash
# 1. Обновить secrets реальными значениями
sudo -u tg-digest vi /opt/tg-digest-secrets/pg_password.txt       # ← сгенерируй сильный пароль
sudo -u tg-digest vi /opt/tg-digest-secrets/tg_api_id.txt        # ← из my.telegram.org
sudo -u tg-digest vi /opt/tg-digest-secrets/tg_api_hash.txt      # ← из my.telegram.org

# 2. Переключиться на пользователя tg-digest (рекомендуется)
sudo -u tg-digest -i
# Теперь все команды docker-compose выполняются БЕЗ sudo

# 3. Клонировать репо и настроить проект
cd /opt/tg-digest
git clone <your-repo-url> .
vi config/channels.yml
vi docker-compose.yml

# 4. Запустить reader
docker-compose build reader
docker-compose up -d reader

# 5. Проверить логи
docker-compose logs -f reader

# 6. После успешного старта, проверить БД
docker-compose exec postgres psql -U tg_digest -d tg_digest -c "SELECT COUNT(*) FROM raw_posts;"
```

**Или выполнять команды без переключения пользователя:**
```bash
sudo -u tg-digest docker-compose -f /opt/tg-digest/docker-compose.yml logs -f reader
```

### Советы по безопасности на сервере

**Структура пользователей и прав:**
```
root                   — только для системных операций
tg-digest (docker)     — все docker-compose операции
```

**PostgreSQL пароль:**
```bash
# Генерируй сильный пароль (32+ символа)
openssl rand -base64 32
```

**Бэкап БД (в cron, от пользователя tg-digest):**
```bash
# Добавь в crontab (sudo -u tg-digest crontab -e)
0 2 * * * cd /opt/tg-digest && docker-compose exec -T postgres pg_dump -U tg_digest tg_digest > /backups/tg_digest_$(date +\%Y\%m\%d).sql
```

**Мониторинг логов:**
```bash
# Уведомления при ошибках в логах
sudo -u tg-digest docker-compose -f /opt/tg-digest/docker-compose.yml logs reader | grep -i error | mail -s "TG-Digest Error" admin@example.com
```

**Обновление Docker образов (еженедельно):**
```bash
# Добавь в crontab (от пользователя tg-digest)
0 3 * * 0 cd /opt/tg-digest && docker-compose pull && docker-compose up -d
```

**Запреты для безопасности:**
```bash
# Убедись что пользователь tg-digest:
chsh -s /bin/false tg-digest        # нет shell доступа
# и
ls -la /var/lib/tg-digest           # нет стандартного окружения
```



### Health checks

```bash
# Проверить, что reader работает
docker-compose ps

# Проверить логи на ошибки
docker-compose logs reader | grep -i error

# Проверить подключение к БД
docker-compose exec reader python -c "
import asyncpg; import asyncio
async def test():
    pool = await asyncpg.create_pool('postgres://tg_digest:***@postgres:5432/tg_digest')
    print('✅ DB connected')
asyncio.run(test())
"
```

## 📊 Архитектура

**Универсальный механизм фильтрации:**

```
Канал [tags: [jobs, news]]
    ↓
    └→ Telegram API (iter_messages, инкрементальная загрузка)
    └→ Для каждого сообщения проверяем фильтры его тегов:
        • Если tag="jobs" → apply_tag_filters(text, tag_filters["jobs"])
        • Если tag="news" → apply_tag_filters(text, tag_filters["news"])
    └→ Сохраняем если ОН ПРОШЁЛ фильтры хотя бы одного тега
    └→ batch insert в raw_posts

PostgreSQL
    ├── raw_posts (посты из каналов с тегами)
    ├── digest_items (обработанные Engine) [в разработке]
    └── published_digests (отправленные Publisher) [в разработке]

Engine (cron)  [в разработке]
    └→ Читает raw_posts по тегам
    └→ LLM обработка

Publisher (cron)  [в разработке]
    └→ Отправка в Telegram
```

### Сценарии использования

**Одиночный тег на канал (рекомендуется):**
```yaml
channels:
  - username: remote_it_jobs
    tags: [jobs]                # Только фильтры для jobs
  
  - username: news_channel
    tags: [news]                # Только для news
```

**Множественные теги на один канал:**
```yaml
channels:
  - username: my_universal_channel
    tags: [jobs, news, learning] # Сохранить если проходит ЛЮБОЙ из фильтров
```

**Все фильтры определяются в одном месте:**
```yaml
tag_filters:
  jobs:                         # Определяется один раз
    include_keywords: [...]
    exclude_keywords: [...]
  
  news:
    include_keywords: ["*"]
  
  learning:
    include_keywords: [...]
```

## ⚙️ Переменные окружения

### Reader

| Переменная | Дефолт | Описание |
|---|---|---|
| `FETCH_MODE` | polling | "once" (один раз) или "polling" (повторно) |
| `POLL_INTERVAL_SEC` | 3600 | интервал между опросами (сек) |
| `BACKFILL_DAYS` | 14 | глубина первого опроса (дни) |
| `CHANNELS_CONFIG` | /app/config/channels.yml | путь к конфигу |
| `DEBUG` | false | включить DEBUG логирование |
| `POSTGRES_USER` | tg_digest | пользователь БД |
| `POSTGRES_DB` | tg_digest | имя БД |
| `DB_POOL_MIN_SIZE` | 5 | минимум открытых подключений TCP к PostgreSQL |
| `DB_POOL_MAX_SIZE` | 20 | максимум одновременных подключений к PostgreSQL |

#### Про DB_POOL_MIN_SIZE и DB_POOL_MAX_SIZE

Это **не про батч вставки сообщений**, а про пул долгоживущих TCP-подключений к PostgreSQL:

```python
# asyncpg.create_pool() создаёт пул переиспользуемых соединений
pool = await asyncpg.create_pool(
    ...,
    min_size=DB_POOL_MIN_SIZE,    # ← сколько всегда держать открытых
    max_size=DB_POOL_MAX_SIZE,    # ← максимум при пиковых нагрузках
)

# Операция 1: SELECT MAX(message_id)
async with pool.acquire() as conn:  # ← берём соединение из пула
    last_message_id = await conn.fetchval(...)  # используем

# Операция 2: INSERT сообщений через одно соединение
async with pool.acquire() as conn:  # ← переиспользуем или берём новое
    await conn.executemany(...)  # вставляем все сообщения батчем
```

**Рекомендации:**
- **Малые серверы** (<512MB): `DB_POOL_MIN_SIZE=1 DB_POOL_MAX_SIZE=5`
- **Стандартные** (1-4GB): `DB_POOL_MIN_SIZE=5 DB_POOL_MAX_SIZE=20` (текущие значения)
- **Высокая нагрузка** (много reader'ов параллельно): `DB_POOL_MIN_SIZE=10 DB_POOL_MAX_SIZE=40`

### Database

| Переменная | Дефолт | Описание |
|---|---|---|
| `POSTGRES_USER` | tg_digest | пользователь |
| `POSTGRES_DB` | tg_digest | БД |
| `POSTGRES_PASSWORD_FILE` | /run/secrets/pg_password | путь к файлу пароля |

## 🚨 Troubleshooting

### `FileNotFoundError: Config file not found`
Проверь что `config/channels.yml` существует и путь в `CHANNELS_CONFIG` верный.

### `Secret [name] not found`
Проверь что все файлы в `../tg-digest-secrets/` существуют и содержат данные.

### `UNIQUE constraint violation: channel, message_id`
Это нормально в первый раз. Означает дублирование при переполрении.

### Connection refused
Проверь что postgres контейнер запущен:
```bash
docker-compose logs postgres
docker-compose ps
```

### Memory issues
Увеличь Docker memory limit и отредактируй пул:
```python
min_size=3,     # уменьшить с 5
max_size=10,    # уменьшить с 20
```

## 📝 Development

### Локальный запуск

```bash
# 1. Python virtualenv
python3.12 -m venv venv
source venv/bin/activate

# 2. Установить зависимости
pip install -r app/reader/src/requirements.txt

# 3. Запустить reader
FETCH_MODE=once python app/reader/src/main.py
```

### Тестирование фильтров

В `config/channels.yml` установи:
```yaml
tag_filters:
  jobs:
    include_keywords:
      - "*"   # Принимай все посты из канала для этого тега
```

## 📄 Лицензия

Приватный проект. Не распространяй без разрешения.

---

## 🔬 Tech Details

### Универсальность Reader

**Фильтрация по тегам** — механизм полностью независим от конкретных тегов (jobs, news, learning, custom и т.д.):

```python
def should_save_post(text: str, channel_tags: list, tag_filters: dict) -> bool:
    """
    Для каждого тега канала проверяем фильтры:
    - Если tag присутствует в tag_filters
    - И текст проходит apply_tag_filters(text, tag_filters[tag])
    - То сохраняем пост
    """
    for tag in channel_tags:
        if tag in tag_filters:
            if apply_tag_filters(text, tag_filters[tag]):
                return True
```

**Расширяемость:** Чтобы добавить новый тег:
1. Определи фильтры в `tag_filters: { my_new_tag: { include_keywords: [...] } }`
2. Добавь `tags: [my_new_tag]` к каналу
3. **Код не меняется** — система работает с любыми тегами

### Производительность

- Инкрементальная загрузка через `min_id` (не переигрываем историю)
- Batch insert 50+ постов за раз (оптимизация БД)
- Connection pooling 5-20 (оптимально для small-medium)
- Индексы на (channel, posted_at) и message_id
- Polling раз в час (минимальная нагрузка)
