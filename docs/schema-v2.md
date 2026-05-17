# Schema v2: двухуровневая схема (raw long + aggregate wide)

## Архитектура

```
imports                  — метаданные каждой выгрузки (источник, период, файл)
query_facts              — LONG: (query|url) × date × metric → value
gsc_facts_by_appearance  — типы сниппетов из GSC
gsc_daily_totals         — Chart.csv GSC (контроль лимита 1000 строк)
metrika_facts_by_source  — разбивка по каналам трафика (Метрика)
query_unified            — WIDE агрегат; полный rebuild после каждого импорта
```

Старая таблица `queries` не трогается до отдельного "ок".

---

## Словари

| Измерение | Допустимые значения                                         |
|-----------|-------------------------------------------------------------|
| source    | `y.direct`, `g.search.console`, `y.metrika`, `topvisor`, `y.webmaster` |
| region    | `moscow`, `moscow.district`, `russia`                       |
| platform  | `desktop`, `mobile`, `tablet`                               |
| project   | открытый словарь (напр. `tempus.ru`)                        |

---

## Таблица `imports`

**Зачем:** единая точка регистрации каждого импортированного файла/папки.
Через `import_id` все факты привязаны к конкретному файлу и периоду.
`file_hash` даёт idempotency — повторный импорт того же файла пропускается.
`filters_json` хранит параметры GSC Filters.csv для аудита выборки.

```sql
CREATE TABLE IF NOT EXISTS imports (
    id           INTEGER PRIMARY KEY,
    project      TEXT NOT NULL,
    domain       TEXT NOT NULL,          -- f"https://{project}"
    source       TEXT NOT NULL,
    region       TEXT NOT NULL,
    platform     TEXT NOT NULL,
    date_from    DATE NOT NULL,
    date_to      DATE NOT NULL,
    folder_path  TEXT NOT NULL,
    file_hash    TEXT,
    imported_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    row_count    INTEGER,
    filters_json JSON,

    CHECK (source   IN ('y.direct','g.search.console','y.metrika','topvisor','y.webmaster')),
    CHECK (region   IN ('moscow','moscow.district','russia')),
    CHECK (platform IN ('desktop','mobile','tablet')),
    CHECK (date_from <= date_to),
    UNIQUE (project, source, region, platform, date_from, date_to, file_hash)
);
```

---

## Таблица `query_facts` (LONG)

**Зачем:** универсальное хранилище для query/url-level данных всех источников
в нормализованном виде. Одна строка = один показатель по одному запросу/урлу
за один день (или NULL-дата = агрегат периода). Позволяет добавлять новые
источники и метрики без изменения схемы.

```sql
CREATE TABLE IF NOT EXISTS query_facts (
    id           INTEGER PRIMARY KEY,
    import_id    INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    query        TEXT,           -- NULL допустим (для Pages / landing_pages)
    url          TEXT,           -- NULL допустим (для Queries / Direct / Topvisor)
    date         DATE,           -- NULL = агрегат за весь период импорта
    traffic_source TEXT,         -- заполняется только y.metrika
    metric       TEXT NOT NULL,
    value        DOUBLE PRECISION,

    CHECK (query IS NOT NULL OR url IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_qf_import  ON query_facts(import_id);
CREATE INDEX IF NOT EXISTS idx_qf_query   ON query_facts(query);
CREATE INDEX IF NOT EXISTS idx_qf_url     ON query_facts(url);
CREATE INDEX IF NOT EXISTS idx_qf_date    ON query_facts(date);
CREATE INDEX IF NOT EXISTS idx_qf_metric  ON query_facts(metric);
```

### Унифицированные имена метрик

```
clicks               impressions          position
ctr                  cost                 conversions
conversion_rate      cpa                  revenue
drr                  visits               users
bounce_rate          page_depth           time_on_site
ecommerce_purchase_rate                   ecommerce_add_to_cart_rate
demand               frequency_exact      frequency_quoted
position_show        position_click       traffic_volume
```

### Правила преобразования при импорте

| Источник      | Сырое поле / формат       | Унифицированная метрика | Преобразование              |
|---------------|---------------------------|-------------------------|-----------------------------|
| y.webmaster   | shows                     | impressions             | —                           |
| y.webmaster   | clicks                    | clicks                  | —                           |
| y.webmaster   | position                  | position                | —                           |
| y.webmaster   | ctr                       | ctr                     | в долях (не %)              |
| y.webmaster   | demand                    | demand                  | —                           |
| g.search.console | CTR "69.23%"           | ctr                     | `/ 100` → 0.6923            |
| y.direct      | CTR "4.5%"                | ctr                     | `/ 100` → 0.045             |
| topvisor      | "--"                      | —                       | пропустить (NULL не пишем)  |
| topvisor      | position_yandex           | position                | см. topvisor_position_yandex в unified |
| topvisor      | frequency                 | frequency_exact         | —                           |
| topvisor      | frequency_quoted          | frequency_quoted        | —                           |

---

## Таблица `gsc_facts_by_appearance`

**Зачем:** GSC отдаёт разбивку по типам сниппетов (Web, Image, Video, FAQ и т.д.)
в отдельном отчёте. Структура отличается от `query_facts` — нет query/url,
есть `appearance_type`. Выделена в отдельную таблицу, чтобы не засорять
`query_facts` строками с NULL query и NULL url.

```sql
CREATE TABLE IF NOT EXISTS gsc_facts_by_appearance (
    id              INTEGER PRIMARY KEY,
    import_id       INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    date            DATE,
    appearance_type TEXT NOT NULL,
    metric          TEXT NOT NULL,
    value           DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_gsc_app_import ON gsc_facts_by_appearance(import_id);
CREATE INDEX IF NOT EXISTS idx_gsc_app_type   ON gsc_facts_by_appearance(appearance_type);
```

---

## Таблица `gsc_daily_totals`

**Зачем:** GSC ограничивает выгрузку строк (лимит 1000). Chart.csv содержит
суточные агрегаты без разбивки по запросам — они позволяют проверить, не
срезаны ли данные в `query_facts`. UNIQUE по (import_id, date, metric)
предотвращает дубли при повторном импорте.

```sql
CREATE TABLE IF NOT EXISTS gsc_daily_totals (
    id        INTEGER PRIMARY KEY,
    import_id INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    date      DATE NOT NULL,
    metric    TEXT NOT NULL,
    value     DOUBLE PRECISION,

    UNIQUE (import_id, date, metric)
);

CREATE INDEX IF NOT EXISTS idx_gsc_dt_import ON gsc_daily_totals(import_id);
CREATE INDEX IF NOT EXISTS idx_gsc_dt_date   ON gsc_daily_totals(date);
```

---

## Таблица `metrika_facts_by_source`

**Зачем:** Метрика даёт разбивку визитов/пользователей по каналам
(organic, direct, cpc, referral и т.д.) в отдельном отчёте. Это не
query-уровень — нет запроса и урла. Выделена отдельно, т.к. нужна
для анализа структуры трафика, а не для запросного анализа.

```sql
CREATE TABLE IF NOT EXISTS metrika_facts_by_source (
    id                      INTEGER PRIMARY KEY,
    import_id               INTEGER NOT NULL REFERENCES imports(id) ON DELETE CASCADE,
    traffic_source_category TEXT,
    traffic_source_detail   TEXT,
    metric                  TEXT NOT NULL,
    value                   DOUBLE PRECISION
);

CREATE INDEX IF NOT EXISTS idx_mfs_import ON metrika_facts_by_source(import_id);
```

---

## Таблица `query_unified` (WIDE, агрегат)

**Зачем:** денормализованное представление для быстрых BI-запросов и API.
Хранит все источники в одной строке по ключу
`(project, query, url, period_from, period_to, region, platform, traffic_source)`.
Пересчитывается целиком после каждого импорта — нет инкрементных обновлений,
нет риска расхождения с `query_facts`.

```sql
CREATE TABLE IF NOT EXISTS query_unified (
    project                   TEXT NOT NULL,
    query                     TEXT,
    url                       TEXT,
    period_from               DATE NOT NULL,
    period_to                 DATE NOT NULL,
    region                    TEXT NOT NULL,
    platform                  TEXT NOT NULL,
    traffic_source            TEXT,

    -- y.direct
    direct_clicks             DOUBLE PRECISION,
    direct_impressions        DOUBLE PRECISION,
    direct_cost               DOUBLE PRECISION,
    direct_ctr                DOUBLE PRECISION,
    direct_position_show      DOUBLE PRECISION,
    direct_conversions        DOUBLE PRECISION,
    direct_revenue            DOUBLE PRECISION,

    -- g.search.console
    gsc_clicks                DOUBLE PRECISION,
    gsc_impressions           DOUBLE PRECISION,
    gsc_position              DOUBLE PRECISION,
    gsc_ctr                   DOUBLE PRECISION,

    -- y.metrika
    ymetrika_visits           DOUBLE PRECISION,
    ymetrika_users            DOUBLE PRECISION,
    ymetrika_bounce_rate      DOUBLE PRECISION,
    ymetrika_page_depth       DOUBLE PRECISION,
    ymetrika_time_on_site     DOUBLE PRECISION,

    -- topvisor (позиции разделены по поисковику — в facts хранятся отдельными метриками)
    topvisor_position_yandex  DOUBLE PRECISION,
    topvisor_position_google  DOUBLE PRECISION,
    topvisor_freq_exact       DOUBLE PRECISION,
    topvisor_freq_quoted      DOUBLE PRECISION,

    -- y.webmaster
    ywebmaster_clicks         DOUBLE PRECISION,
    ywebmaster_impressions    DOUBLE PRECISION,
    ywebmaster_position       DOUBLE PRECISION,
    ywebmaster_ctr            DOUBLE PRECISION,
    ywebmaster_demand         DOUBLE PRECISION,

    PRIMARY KEY (project, query, url, period_from, period_to, region, platform, traffic_source)
);
```

### Правила агрегации при pivot

| Метрика                          | Агрегация |
|----------------------------------|-----------|
| clicks, impressions, cost, visits, users, conversions, revenue | SUM |
| position, ctr, bounce_rate, page_depth, time_on_site, cpa, drr | AVG |
| frequency_exact, frequency_quoted, demand, traffic_volume | MAX (берём последнее актуальное) |

---

## Связи

```
imports (1) ──< query_facts               (N)  ON DELETE CASCADE
imports (1) ──< gsc_facts_by_appearance   (N)  ON DELETE CASCADE
imports (1) ──< gsc_daily_totals          (N)  ON DELETE CASCADE
imports (1) ──< metrika_facts_by_source   (N)  ON DELETE CASCADE
query_unified — независимая, полный rebuild при каждом импорте
```

---

## Замечание по topvisor в `query_facts`

В `query_facts` позиции topvisor хранятся с метриками
`position_yandex` и `position_google` (не просто `position`),
чтобы при pivot в `query_unified` можно было направить их
в разные колонки (`topvisor_position_yandex` / `topvisor_position_google`).
