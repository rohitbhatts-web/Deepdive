# Hosted recorded demo: the production React build and the FastAPI backend behind ONE origin, one worker, read-only.
# Build:  docker build -t deepdive-demo .      Run:  docker run --rm -p 8000:8000 -v deepdive-data:/data deepdive-demo
FROM node:22.11.0-slim AS web
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 UV_LINK_MODE=copy PATH="/app/.venv/bin:$PATH"
RUN pip install --no-cache-dir uv==0.11.32
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project --no-cache
COPY backend ./backend
COPY demo ./demo
COPY --from=web /build/frontend/dist ./frontend/dist
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh && useradd --system --uid 10001 --home-dir /app app && mkdir /data && chown app:app /data

# No provider key is set or needed: the backend refuses every write, opens SQLite read-only and builds no model/search client.
ENV PUBLIC_READ_ONLY=1 DATABASE_PATH=/data/deepdive.db PORT=8000
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/health'%os.environ.get('PORT','8000'),timeout=4)"
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn backend.public:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
