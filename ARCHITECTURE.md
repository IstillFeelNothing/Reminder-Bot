# Архітектура проєкту

Проєкт побудований за принципами інверсії залежностей (Dependency Inversion): доменний і сервісний шари працюють виключно через абстракції (`Protocol`) і не мають жодної залежності від `aiosqlite` чи `aiogram`.

## Структура модулів

| Файл / пакет | Відповідальність | Від кого залежить |
| :--- | :--- | :--- |
| `domain/models.py` | Незмінні сутності (`@dataclass(frozen=True)`): `User`, `Reminder` та `Enum` статусів `ReminderStatus` (`pending`, `sent`, `failed`) | — (тільки стандартна бібліотека) |
| `domain/exceptions.py` | Доменні винятки: помилки формату дати/часу, минулий час, весняна прогалина DST, доступ до чужого запису (`AccessDeniedError`) та помилки доставки (`RecipientUnavailableError`, `RateLimitExceededError`, `TemporaryDeliveryError`) | — |
| `domain/validators.py` | Валідація тексту нагадування, парсинг дати/часу, виявлення неіснуючого часу під час переведення годинника, обчислення `trigger_at_utc` (`fold=0`) | `domain/exceptions.py` |
| `domain/protocols.py` | Контракти (`typing.Protocol`): `UserRepositoryProtocol`, `ReminderRepositoryProtocol`, `UnitOfWorkProtocol`, `NotificationSenderProtocol` | `domain/models.py` |
| `services/reminder_service.py` | Бізнес-логіка: створення нагадувань, перевірка прав власника (`user_id`), зміна поясу з масовим перерахунком UTC усередині `UnitOfWork` | `domain/models.py`, `domain/validators.py`, `domain/exceptions.py`, `domain/protocols.py` |
| `services/scheduler.py` | Фонова корутина: періодична вибірка дозрілих нагадувань, групування прострочених записів, делегування відправки через `NotificationSenderProtocol`, реакція на `RecipientUnavailableError`, `RateLimitExceededError`, `TemporaryDeliveryError` та кооперативна зупинка | `domain/models.py`, `domain/exceptions.py`, `domain/protocols.py` |
| `storage/database.py` | Підключення до SQLite через `aiosqlite`, увімкнення `PRAGMA foreign_keys = ON`, застосування DDL-схеми та реалізація `SQLiteUnitOfWork` | `aiosqlite`, `domain/protocols.py` |
| `storage/repositories.py` | Асинхронні репозиторії `SQLiteUserRepository` та `SQLiteReminderRepository` (CRUD, вибірка планувальника, фізичний `DELETE` при видаленні) | `aiosqlite`, `domain/models.py`, `domain/protocols.py` |
| `storage/fsm.py` | Персистентне сховище станів діалогу (`SQLiteStorage`) — реалізація `BaseStorage` для `aiogram` у таблиці `fsm_states` з нормалізацією ключів (`thread_id`, `business_connection_id`, `destiny`) | `aiogram`, `storage/database.py` |
| `bot/formatters.py` | Форматування списків активних і прострочених нагадувань для користувача, розбивка довгих повідомлень на чанки (по 3800–4000 символів) | `domain/models.py` |
| `bot/sender.py` | Реалізація `NotificationSenderProtocol` поверх `aiogram.Bot`: форматування повідомлень, відправка чанків та перетворення винятків Telegram API у доменні помилки доставки (`domain/exceptions.py`) | `aiogram`, `domain/models.py`, `domain/exceptions.py`, `domain/protocols.py`, `bot/formatters.py` |
| `bot/keyboards.py` | Генерація інлайн-клавіатур для вибору IANA-поясу, керування списком нагадувань та скасування покрокового діалогу | `aiogram` |
| `bot/handlers/` | Маршрутизація команд (`/start`, `/add`, `/list`, `/timezone`), обробка кроків FSM та натискань інлайн-кнопок | `aiogram`, `services/reminder_service.py`, `bot/formatters.py`, `bot/keyboards.py` |
| `main.py` | Точка входу: завантаження конфігурації (токен, шлях до БД), збірка залежностей (DI), запуск polling та фонового планувальника | Усі модулі верхнього рівня (`storage`, `services`, `bot`) |

## Потік залежностей

```text
main.py
 ├──► bot/handlers/ ──► services/reminder_service.py ──► domain/protocols.py ◄── storage/repositories.py
 │         │                     │                                 ▲               storage/database.py
 │         ▼                     ▼                                 │
 │    bot/keyboards.py    domain/validators.py                     │
 │    bot/formatters.py   domain/exceptions.py                     │
 │                                                                 │
 └──► services/scheduler.py ───────────────────────────────────────┘
             │                                                     ▲
             ▼                                                     │
      domain/exceptions.py ◄────────────────────────────── bot/sender.py ──► bot/formatters.py
                                                                   │
                                                                   ▼
                                                                aiogram
```

* **Ядро (`domain/` + `services/`):** жодного імпорту `aiogram` чи `aiosqlite`. Обидва сервіси (`ReminderService` і `Scheduler`) покриваються юніт-тестами через in-memory фейки протоколів.
* **Транзакційність (`SQLiteUnitOfWork`):** керує єдиним з'єднанням `aiosqlite` та межами `BEGIN ... COMMIT / ROLLBACK` під час оновлення поясу користувача і перерахунку його активних нагадувань.
* **Ізоляція Telegram (`bot/sender.py`):** приховує деталі `aiogram` і ліміту 4096 символів від планувальника за інтерфейсом `NotificationSenderProtocol`.

## Схема БД (SQLite)

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id    INTEGER PRIMARY KEY,
    timezone   TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reminders (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    chat_id        INTEGER NOT NULL,
    text           TEXT NOT NULL,
    local_datetime TEXT NOT NULL,
    trigger_at_utc TEXT NOT NULL,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending', 'sent', 'failed')),
    created_at     TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (user_id) ON DELETE CASCADE
);

-- Складений індекс для планувальника: рівність (status) + діапазон (trigger_at_utc)
CREATE INDEX IF NOT EXISTS idx_reminders_scheduler
    ON reminders (status, trigger_at_utc);

-- Індекс для списку нагадувань користувача (/list) та перерахунку при зміні поясу
CREATE INDEX IF NOT EXISTS idx_reminders_user_status
    ON reminders (user_id, status, trigger_at_utc);

-- Сховище станів aiogram BaseStorage (None нормалізується в 0 / '' для коректного PK у SQLite)
CREATE TABLE IF NOT EXISTS fsm_states (
    bot_id                 INTEGER NOT NULL,
    chat_id                INTEGER NOT NULL,
    user_id                INTEGER NOT NULL,
    thread_id              INTEGER NOT NULL DEFAULT 0,
    business_connection_id TEXT NOT NULL DEFAULT '',
    destiny                TEXT NOT NULL DEFAULT 'default',
    state                  TEXT,
    data                   TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (bot_id, chat_id, user_id, thread_id, business_connection_id, destiny)
);
```

### Опис таблиць та правила роботи з даними

* **`users`:** зберігає профіль користувача (`user_id`), його поточний часовий пояс у форматі IANA (`timezone`, наприклад `Europe/Kyiv`) та час реєстрації у UTC (`created_at`). Запис створюється лише після підтвердження вибору поясу під час `/start`.
* **`reminders`:** зберігає намір користувача (`local_datetime` у форматі `YYYY-MM-DDTHH:MM:SS`) та обчислений момент спрацювання у канонічному UTC (`trigger_at_utc` у форматі `YYYY-MM-DDTHH:MM:SSZ`, рівно 20 символів для точного посимвольного порівняння).
  * `AUTOINCREMENT` запобігає повторній видачі ID щойно видаленого останнього запису, захищаючи від помилкового видалення через старі інлайн-кнопки.
  * Допустимі статуси обмежені через `CHECK`: `'pending'`, `'sent'`, `'failed'`. Видалені користувачем нагадування видаляються фізично (`DELETE`).
* **`fsm_states`:** персистентне сховище станів діалогів `aiogram`. Поля `thread_id = None` та `business_connection_id = None` нормалізуються у `0` та `''` відповідно, щоб уникнути дублікатів у складеному `PRIMARY KEY` SQLite (де `NULL != NULL`). Таблиця не має зовнішнього ключа на `users`, оскільки діалог вибору поясу відбувається до створення запису користувача.
* **Керування з'єднанням:** `PRAGMA foreign_keys = ON;` виконується в коді `storage/database.py` під час відкриття кожного з'єднання `aiosqlite`, оскільки в SQLite цей параметр не зберігається у файлі БД.