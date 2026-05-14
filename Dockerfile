FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY frontend/ frontend/
COPY data/ data/
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN mkdir -p /data

ENV DB_PATH=/data/seo.duckdb
ENV DATA_DIR=data
ENV PORT=8000

EXPOSE 8000

# entrypoint чистит битые DuckDB-файлы при каждом старте, затем запускает uvicorn
CMD ["./entrypoint.sh"]
