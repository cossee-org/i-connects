# syntax=docker/dockerfile:1.7
# --- Stage 1: build Tailwind/DaisyUI CSS ---
FROM node:22-bookworm-slim AS css-builder

WORKDIR /src

# Lockfile + `npm ci`: the exact dependency tree CI builds and tests against,
# not whatever `npm install` resolves on the day.
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

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
    DJANGO_SETTINGS_MODULE=config.settings.prod

# curl is for the container health checks. No compiler and no libpq headers:
# every requirement installs from a wheel (psycopg[binary] bundles libpq) or
# is pure Python -- verified with `pip install --only-binary=:all:` for
# cp312/manylinux. Keep it that way when adding dependencies, or add
# build-essential/libpq-dev back here.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

# Application code stays root-owned and read-only to appuser: nothing writes
# here at runtime (PYTHONDONTWRITEBYTECODE is set above). Only the two
# directories the app actually writes to are handed to appuser below.
COPY . .

# Bring in the Tailwind-compiled CSS from the css-builder stage.
COPY --from=css-builder /src/static/css/output.css ./static/css/output.css

RUN mkdir -p /app/staticfiles /app/media \
 && chown appuser:appuser /app/staticfiles /app/media

USER appuser

# collectstatic at build time, so a container start only has to run migrate
# and the image is self-contained. prod settings insist on a SECRET_KEY at
# import; the value here exists only for this step and is not baked into the
# image's environment. collectstatic runs no system checks and opens no
# database connection.
RUN DJANGO_SECRET_KEY=build-only DJANGO_ALLOWED_HOSTS=build \
    python manage.py collectstatic --noinput

EXPOSE 8000

# No HEALTHCHECK instruction on purpose: this one image runs as web, celery
# and celery-beat, each with a different notion of "healthy". The checks live
# per service in docker-compose.yml / docker-compose.prod.yml.

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--graceful-timeout", "30"]
