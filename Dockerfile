# syntax=docker/dockerfile:1.7
# --- Stage 1: build Tailwind/DaisyUI CSS ---
FROM node:20-bookworm-slim AS css-builder

WORKDIR /src

COPY package.json ./
RUN npm install --no-audit --no-fund

COPY tailwind.config.js postcss.config.js ./
COPY static ./static
COPY templates ./templates
COPY apps ./apps

RUN npx tailwindcss -i ./static/css/input.css -o ./static/css/output.css --minify


# --- Stage 2: Python runtime ---
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_SETTINGS_MODULE=config.settings.dev

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential \
      libpq-dev \
      curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY . .

# Bring in the Tailwind-compiled CSS from the css-builder stage.
COPY --from=css-builder /src/static/css/output.css ./static/css/output.css

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3"]
