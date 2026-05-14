#!/bin/sh
# entrypoint.sh — запускается вместо CMD в Dockerfile.
#
# Зачем: Railway при ON_FAILURE restart не пересоздаёт /data.
# Если DuckDB упал на полуслове — файл остаётся битым и зависает при следующем старте.
# Решение: сносим DB-файл перед каждым запуском. Данные всегда реимпортируются
# из data/ (она в git-репо и запечена в Docker-образ), поэтому это безопасно.

DB="${DB_PATH:-/data/seo.duckdb}"

echo "[entrypoint] DB_PATH=$DB"

# Чистим все возможные DuckDB-артефакты прошлого запуска
for ext in "" ".wal" ".tmp"; do
    f="$DB$ext"
    if [ -f "$f" ]; then
        echo "[entrypoint] removing stale file: $f"
        rm -f "$f"
    fi
done

echo "[entrypoint] starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
