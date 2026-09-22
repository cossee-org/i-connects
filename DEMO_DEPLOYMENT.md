# Demo Deployment — retired

This guide used to describe a standalone GCP VM running its own `caddy`
compose service, unauthenticated Redis, and `ports: []` overrides. The
committed compose files no longer match any of that: there is no `caddy`
service (production sits behind a host-level Caddy), `REDIS_PASSWORD` is
mandatory, `web` binds to loopback only, `collectstatic` runs at image build,
and the data volumes are external. Following the old text produced a stack
in which Redis never became healthy and nothing else started.

**Use `DEPLOYMENT.md` for any host, demo or production.** For a throwaway
single-app VM that has no host Caddy of its own, the only additions are:

1. A `caddy` service in a *local, uncommitted* override file, using a copy of
   the repo `Caddyfile` with the domain changed and the upstream set to
   `web:8000` (Docker DNS) instead of `127.0.0.1:8000`:

   ```yaml
   # docker-compose.caddy.yml -- add as a third -f file
   services:
     caddy:
       image: caddy:2
       restart: unless-stopped
       ports: ["80:80", "443:443"]
       volumes:
         - ./Caddyfile:/etc/caddy/Caddyfile:ro
         - caddy_data:/data
         - caddy_config:/config
       depends_on:
         web:
           condition: service_healthy
   volumes:
     caddy_data:
     caddy_config:
   ```

2. `DJANGO_ALLOWED_HOSTS` and `SITE_URL` set to the demo domain in `.env`.

Everything else — `.env`, the external data volumes, health checks, backups,
the restart-unhealthy cron — is exactly as `DEPLOYMENT.md` describes.
