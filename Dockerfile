FROM node:22-bookworm-slim AS ui-build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts ./
COPY frontend/ ./frontend/
RUN npm run build

FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    APP_ENV_FILE=/app/data/.env \
    VNU_BROWSER_STATE_FILE=/app/data/vnu-browser-state.json \
    VNU_BROWSER_PROFILE_DIR=/app/.vnu-browser-profile

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY . .
COPY --from=ui-build /app/static/build/ /app/static/build/
RUN chmod +x /app/docker/entrypoint.sh \
    && mkdir -p /app/data /app/downloads /app/.vnu-browser-profile

EXPOSE 8543
ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["python", "app.py"]
