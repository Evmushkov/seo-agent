# SEO Content Agent v2 — Архитектура

## 1. Общая идея

Автономный SEO-агент, который работает по принципу **«данные → теги → ядро → страницы → анализ»**.

В отличие от текущей версии (v1), которая сначала обходит сайт и угадывает ключевые слова, v2 начинает с реальных данных поиска из 5 источников, расставляет теги по настраиваемым правилам, формирует семантическое ядро, и только потом идёт на конкретные приоритетные страницы и анализирует их контент.

---

## 2. Источники данных

| # | Источник | Формат загрузки | Ключевые метрики | Объём |
|---|----------|-----------------|-------------------|-------|
| 1 | Google Search Console | CSV (внутри ZIP) | query, page, clicks, impressions, CTR, position | ~1K запросов, ~1K страниц |
| 2 | Яндекс.Вебмастер | XLSX wide-формат | query, url, shows, position, demand, ctr, clicks × период | 15K-96K пар |
| 3 | Яндекс.Метрика | CSV | страница, источник, фраза, визиты, отказы, глубина, время, цели, доход, конверсия | ~4.5K строк |
| 4 | TopVisor | XLSX wide-формат | запрос, частотность (Вордстат), позиции по дням | ~916 запросов × 32 даты |
| 5 | Яндекс.Директ | CSV | поисковый запрос, показы, клики, расход, конверсии, CR, доход, CPA, отказы, глубина | ~652K запросов |

### 2.1. Метаданные загрузки

Каждый файл при загрузке получает метаданные:

- `source` — источник (gsc / webmaster / metrika / topvisor / direct)
- `region` — регион (russia / moscow)
- `platform` — платформа (mobile / desktop / all)
- `search_engine` — поисковая система (yandex / google / all)
- `period_start`, `period_end` — период данных
- `granularity` — гранулярность (daily / monthly / total)
- `uploaded_at` — когда загружен
- `filename` — исходное имя файла

### 2.2. Обновление данных

- Частота: раз в неделю
- Способ: `git push` новых файлов в папку `data/`
- При деплое Railway агент сканирует `data/`, парсит новые файлы, импортирует в DuckDB
- В git хранится только последняя версия файлов (перезаписываются)
- Вся история хранится в DuckDB на Railway Volume (`/data/seo.duckdb`)
- В интерфейсе — индикатор свежести каждого источника

---

## 3. Схема базы данных (DuckDB)

### 3.1. Таблица `uploads` — реестр загрузок

```sql
CREATE TABLE uploads (
    id              VARCHAR PRIMARY KEY,  -- UUID
    source          VARCHAR NOT NULL,     -- gsc|webmaster|metrika|topvisor|direct
    region          VARCHAR DEFAULT 'russia',
    platform        VARCHAR DEFAULT 'mobile',
    search_engine   VARCHAR DEFAULT 'all',
    period_start    DATE,
    period_end      DATE,
    granularity     VARCHAR,              -- daily|monthly|total
    filename        VARCHAR,
    rows_imported   INTEGER,
    uploaded_at     TIMESTAMP DEFAULT now()
);
```

### 3.2. Таблица `search_queries` — все запросы из всех источников (нормализованный формат)

```sql
CREATE TABLE search_queries (
    upload_id       VARCHAR REFERENCES uploads(id),
    source          VARCHAR NOT NULL,     -- gsc|webmaster|direct
    date            DATE,                 -- конкретная дата или первое число месяца
    query           VARCHAR NOT NULL,
    url             VARCHAR,              -- страница (если есть привязка)
    region          VARCHAR,
    platform        VARCHAR,
    search_engine   VARCHAR,
    -- метрики
    shows           INTEGER DEFAULT 0,
    clicks          INTEGER DEFAULT 0,
    ctr             DOUBLE DEFAULT 0,
    position        DOUBLE DEFAULT 0,
    demand          INTEGER DEFAULT 0,    -- частотность (Вебмастер/Вордстат)
    -- метрики Директа
    spend           DOUBLE DEFAULT 0,
    conversions     INTEGER DEFAULT 0,
    cr              DOUBLE DEFAULT 0,
    revenue         DOUBLE DEFAULT 0,
    cpa             DOUBLE DEFAULT 0,
    bounce_rate     DOUBLE DEFAULT 0,
    depth           DOUBLE DEFAULT 0
);
```

### 3.3. Таблица `page_metrics` — данные по страницам из Метрики

```sql
CREATE TABLE page_metrics (
    upload_id       VARCHAR REFERENCES uploads(id),
    source          VARCHAR NOT NULL,     -- metrika
    url             VARCHAR NOT NULL,
    traffic_source  VARCHAR,              -- Яндекс|Google|Директ|...
    search_phrase   VARCHAR,
    visits          INTEGER DEFAULT 0,
    visitors        INTEGER DEFAULT 0,
    bounce_rate     DOUBLE DEFAULT 0,
    depth           DOUBLE DEFAULT 0,
    time_on_site    VARCHAR,
    goal_completions INTEGER DEFAULT 0,
    revenue         DOUBLE DEFAULT 0,
    conversion      DOUBLE DEFAULT 0
);
```

### 3.4. Таблица `position_tracking` — позиции из TopVisor

```sql
CREATE TABLE position_tracking (
    upload_id       VARCHAR REFERENCES uploads(id),
    query           VARCHAR NOT NULL,
    search_engine   VARCHAR NOT NULL,     -- yandex|google
    region          VARCHAR,
    platform        VARCHAR,
    date            DATE NOT NULL,
    position        INTEGER,              -- NULL = не в выдаче
    frequency_exact INTEGER,              -- "!Частота" из Вордстат
    frequency_phrase INTEGER              -- "[!Частота]" из Вордстат
);
```

### 3.5. Таблица `tags` — определения тегов (конструктор)

```sql
CREATE TABLE tags (
    id              VARCHAR PRIMARY KEY,
    name            VARCHAR NOT NULL,
    icon            VARCHAR DEFAULT '🏷️',
    description     VARCHAR,
    formula         JSON NOT NULL,        -- правило в формате JSON (см. ниже)
    sort_order      INTEGER DEFAULT 0,
    is_active       BOOLEAN DEFAULT true,
    created_at      TIMESTAMP DEFAULT now()
);
```

### 3.6. Таблица `query_tags` — присвоенные теги (результат вычисления)

```sql
CREATE TABLE query_tags (
    query           VARCHAR NOT NULL,
    url             VARCHAR,
    tag_id          VARCHAR REFERENCES tags(id),
    computed_at     TIMESTAMP DEFAULT now(),
    meta            JSON                  -- доп. данные (значение метрики, период)
);
```

### 3.7. Таблица `semantic_core` — семантическое ядро (результат AI-анализа)

```sql
CREATE TABLE semantic_core (
    id              VARCHAR PRIMARY KEY,
    created_at      TIMESTAMP DEFAULT now(),
    model           VARCHAR,
    core_data       JSON,                 -- полное ядро
    status          VARCHAR DEFAULT 'draft'
);
```

### 3.8. Таблица `audit_reports` — отчёты аудита

```sql
CREATE TABLE audit_reports (
    id              VARCHAR PRIMARY KEY,
    created_at      TIMESTAMP DEFAULT now(),
    domain          VARCHAR,
    url             VARCHAR,
    status          VARCHAR DEFAULT 'running',
    core_id         VARCHAR REFERENCES semantic_core(id),
    summary         JSON,
    pages           JSON,
    model           VARCHAR
);
```

---

## 4. Формат формулы тега (конструктор)

Каждый тег — это правило, которое вычисляется по данным. Формат JSON:

```json
{
  "conditions": [
    {
      "source": "webmaster",
      "metric": "clicks",
      "aggregation": "sum",
      "period": "last_3_months",
      "operator": ">=",
      "value": 100
    },
    {
      "logic": "AND",
      "source": "webmaster",
      "metric": "position",
      "aggregation": "avg",
      "period": "last_month",
      "operator": "<=",
      "value": 3
    }
  ]
}
```

### 4.1. Доступные поля формулы

**source** (источник данных):
- `gsc` — Google Search Console
- `webmaster` — Яндекс.Вебмастер
- `metrika` — Яндекс.Метрика
- `topvisor` — TopVisor
- `direct` — Яндекс.Директ
- `any` — любой источник

**metric** (метрика):
- `clicks`, `shows` (impressions), `ctr`, `position`
- `demand` (частотность)
- `revenue`, `conversions`, `cr`, `spend`, `cpa`
- `bounce_rate`, `depth`
- `visits`, `visitors`, `goal_completions`, `conversion`
- `frequency_exact`, `frequency_phrase` (Вордстат)
- `traffic_share` (доля от общего трафика, вычисляемая)
- `cannibalization_count` (кол-во URL по одному запросу, вычисляемая)
- `position_in_top3_pct` (% дней в топ-3, вычисляемая)
- `position_in_top10_pct` (% дней в топ-10, вычисляемая)
- `position_trend` (изменение позиции за период, вычисляемая)

**aggregation** (агрегация):
- `sum`, `avg`, `min`, `max`, `count`
- `pct` — процентиль
- `share` — доля от общего

**period** (период):
- `last_week`, `last_2_weeks`, `last_month`, `last_3_months`, `all_time`
- `custom` — с указанием дат

**operator** (оператор сравнения):
- `>=`, `<=`, `>`, `<`, `==`, `!=`, `between`

**logic** (связка между условиями):
- `AND`, `OR`

### 4.2. Предустановленные теги

| Иконка | Название | Формула (упрощённо) |
|--------|----------|---------------------|
| 🐄 | Кормилец | traffic_share >= 80% (по кликам) |
| 💰 | Конвертер | metrika.conversion >= {порог} OR direct.cr >= {порог} |
| 🏆 | Топ-3 | topvisor.position_in_top3_pct >= {порог}% |
| 🎯 | Почти топ | webmaster.position avg between 4 and 10 |
| 📈 | Потенциал | webmaster.position avg between 11 and 50 |
| 👻 | Невидимка | webmaster.position avg > 50 OR shows == 0 |
| 🔥 | Высокий спрос | topvisor.frequency_exact >= {порог} |
| 💎 | Рекл. конвертер | direct.cr >= {порог} AND direct.conversions >= {мин} |
| ⚔️ | Каннибал | cannibalization_count >= 2 |
| 👀 | Низкий CTR | shows >= {порог} AND ctr <= {порог}% |
| 📉 | Падает | position_trend > +{порог} (позиция ухудшилась) |
| 📈↑ | Растёт | position_trend < -{порог} (позиция улучшилась) |

Пороговые значения `{порог}` — редактируемые в интерфейсе.

---

## 5. Flow работы агента

```
Загрузка данных (git push → deploy → import)
         │
         ▼
┌─────────────────────────┐
│  1. ИМПОРТ              │
│  Парсинг файлов из      │
│  data/ → DuckDB         │
│  (нормализация форматов)│
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  2. ТЕГИРОВАНИЕ         │
│  Вычисление тегов по    │
│  формулам из конструктора│
│  Каждый запрос/URL      │
│  получает набор тегов   │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  3. ЯДРО                │
│  AI анализирует          │
│  данные + теги и строит │
│  семантическое ядро:    │
│  группы запросов,       │
│  приоритеты страниц     │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  4. АУДИТ КОНТЕНТА      │
│  Агент идёт на          │
│  приоритетные страницы, │
│  парсит HTML, считает   │
│  частоты слов,          │
│  сравнивает с ключами   │
│  из ядра, даёт оценку   │
└──────────┬──────────────┘
           │
           ▼
┌─────────────────────────┐
│  5. ОТЧЁТ               │
│  Результат с графиками, │
│  рекомендациями,        │
│  сравнением с прошлым   │
│  периодом               │
└─────────────────────────┘
```

---

## 6. Структура проекта

```
seo-agent/
├── app/
│   ├── main.py             # FastAPI: роуты, оркестрация
│   ├── db.py               # DuckDB: подключение, миграции
│   ├── importers/           # Парсеры для каждого источника
│   │   ├── gsc.py           # Google Search Console (CSV/ZIP)
│   │   ├── webmaster.py     # Яндекс.Вебмастер (XLSX wide)
│   │   ├── metrika.py       # Яндекс.Метрика (CSV)
│   │   ├── topvisor.py      # TopVisor (XLSX wide)
│   │   ├── direct.py        # Яндекс.Директ (CSV)
│   │   └── base.py          # Базовый класс импортера
│   ├── tagger.py            # Движок тегов (вычисление по формулам)
│   ├── ai.py                # OpenRouter: ядро + рекомендации
│   ├── crawler.py           # Парсинг HTML страниц
│   ├── analyzer.py          # Анализ частоты слов
│   └── auth.py              # OAuth (GSC API — на будущее)
├── frontend/
│   └── index.html           # SPA-дашборд
├── data/                    # CSV/XLSX файлы (только последняя версия)
│   ├── gsc/
│   ├── webmaster/
│   ├── metrika/
│   ├── topvisor/
│   └── direct/
├── Dockerfile
├── railway.json
├── requirements.txt
└── README.md
```

---

## 7. API-эндпоинты

### 7.1. Данные

```
POST   /api/import                  — импорт всех файлов из data/
POST   /api/import/upload           — загрузка файла через веб-интерфейс
GET    /api/sources                 — статус источников (свежесть, кол-во записей)
GET    /api/queries                 — список запросов с тегами (пагинация, фильтры)
GET    /api/pages                   — список страниц с метриками
GET    /api/cannibalization         — запросы с 2+ URL
```

### 7.2. Теги

```
GET    /api/tags                    — все теги
POST   /api/tags                    — создать тег
PUT    /api/tags/{id}               — изменить тег
DELETE /api/tags/{id}               — удалить тег
POST   /api/tags/compute            — пересчитать все теги
GET    /api/tags/{id}/queries       — запросы с этим тегом
```

### 7.3. Ядро и аудит

```
POST   /api/core/build              — построить семантическое ядро
GET    /api/core                    — текущее ядро
POST   /api/audit                   — запустить аудит контента
GET    /api/audit/{id}              — статус/результат аудита
GET    /api/audits                  — история аудитов
```

### 7.4. Фильтры (применяются ко всем GET-запросам)

```
?region=russia|moscow
?platform=mobile|desktop
?search_engine=yandex|google|all
?period=last_week|last_month|last_3_months|custom
?date_from=2026-01-01&date_to=2026-05-01
```

---

## 8. Интерфейс (экраны)

### 8.1. Главная — Дашборд

- Статус источников данных (свежесть каждого, кнопка «Импортировать»)
- Фильтры: регион, платформа (мобильные по умолчанию)
- Сводка: всего запросов, страниц, распределение по тегам
- Топ проблем: каннибализация, низкий CTR, падающие позиции

### 8.2. Запросы

- Таблица всех запросов с тегами (фильтрация по тегам, сортировка)
- Клик на запрос → детали: все источники, позиции по времени, связанные URL

### 8.3. Страницы

- Таблица страниц с агрегированными метриками
- Клик на страницу → запросы, которые ведут на эту страницу, контент-анализ

### 8.4. Конструктор тегов

- Список тегов с переключателями вкл/выкл
- Создание/редактирование тега:
  - Название, иконка, описание
  - Визуальный конструктор формулы: [источник] → [метрика] → [агрегация] → [оператор] → [значение]
  - Кнопка «Добавить условие» (AND/OR)
  - Превью: сколько запросов попадут под этот тег
- Кнопка «Пересчитать все теги»

### 8.5. Семантическое ядро

- Группы запросов по категориям
- Привязка запросов к страницам
- Приоритеты
- Кнопка «Построить ядро» (с выбором AI-модели)

### 8.6. Аудит контента

- Запуск аудита по приоритетным страницам из ядра
- Результаты: оценка контента, графики частоты слов, рекомендации
- Сравнение с предыдущим аудитом

### 8.7. Настройки

- Выбор AI-модели (OpenRouter)
- Управление API-ключами (будущее)
- Экспорт данных

---

## 9. Стек технологий

| Компонент | Технология | Причина |
|-----------|-----------|---------|
| Backend | Python + FastAPI | Простота, async, уже используется |
| БД | DuckDB | OLAP-аналитика, встраиваемая, быстрые агрегации по 800K+ строк |
| AI | OpenRouter API | Мульти-модель, один ключ |
| Frontend | Vanilla HTML/JS | Один файл, минимум зависимостей |
| Деплой | Docker + Railway | Уже настроено |
| Хранение | Railway Volume /data | Персистентный DuckDB |
| Код | GitHub | Версионирование, CI/CD через Railway |

---

## 10. Приоритеты реализации

### Фаза 1 — MVP (первый рабочий результат)
1. DuckDB + схема таблиц
2. Импортеры для всех 5 источников
3. Предустановленные теги (без конструктора)
4. Интерфейс: дашборд + таблица запросов с тегами
5. Деплой на Railway

### Фаза 2 — Ядро и аудит
6. Конструктор тегов в интерфейсе
7. AI-генерация семантического ядра на основе данных + тегов
8. Аудит контента приоритетных страниц
9. Сравнение аудитов (до/после)

### Фаза 3 — Автоматизация
10. API-подключения (GSC, Вебмастер, Метрика)
11. Обновление по расписанию
12. Уведомления (Telegram)
13. Экспорт отчётов в PDF

---

## 11. Ограничения и риски

- **Размер данных**: файл Директа ~650K строк. DuckDB справится, но импорт займёт несколько секунд. Ограничение Railway по памяти (512MB на бесплатном тарифе) — нужно следить.
- **AI-стоимость**: построение ядра на 15K+ запросах потребует суммаризации данных перед отправкой в AI. Нельзя отправить все 800K строк в промпт.
- **Git LFS**: если файлы данных вырастут выше 100MB, GitHub будет ограничивать. Решение — .gitignore на data/ и ручная загрузка через веб или scp.
- **Поисковые фразы Google**: Google не отдаёт поисковые фразы в Метрику (всё «Not provided»). Связка запрос→конверсия для Google возможна только через GSC + косвенные данные.
