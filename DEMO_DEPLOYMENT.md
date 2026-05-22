# Demo Deployment — i-connects on a GCP VM

End-to-end instructions for bringing the demo up on an existing Google Cloud
Compute Engine VM, accessed via the browser **Console SSH** window. Domain:
`demo.i-connects.cossee.org`. Stack: Docker Compose (Django + Postgres +
Redis + Celery + Beat), fronted by Caddy with Let's Encrypt TLS.

The committed `docker-compose.yml` is the **development** stack
(`runserver`, source bind-mount). The production override is
`docker-compose.prod.yml` plus the `Caddyfile` — both at the repo root.

---

## 1. Pre-flight on the VM

Open Console SSH (Compute Engine → VM → **SSH** dropdown). Then:

```bash
cat /etc/os-release && uname -m
docker --version && docker compose version          # may be missing — see step 2
sudo ss -tlnp | grep -E ':80|:443|:8000' || echo "ports free"
df -h /                                              # need ~5 GB free
```

In the GCP Console also confirm:

- **Static external IP** is attached to the VM (Networking → IP addresses;
  promote the ephemeral address to static if needed). Caddy needs a stable IP
  so the cert renews cleanly.
- **Firewall** allows `tcp:80` and `tcp:443` to the VM's network tag (the
  default `default-allow-http` / `default-allow-https` rules cover this if
  the VM carries the `http-server` / `https-server` tags).
- **DNS**: `demo.i-connects.cossee.org` A-record points at the static IP.
  Verify from anywhere with `dig +short demo.i-connects.cossee.org` — Caddy
  will fail to issue the cert until this resolves.

## 2. Install Docker (skip if already present)

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl git
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
exit
```

Re-open the Console SSH tab so the new group membership takes effect; `docker
ps` should then work without `sudo`.

## 3. Get the code

The repo is private. Pick one:

- **HTTPS + GitHub PAT** — generate a fine-grained read-only token scoped to
  `cossee-org/i-connects`, paste it when prompted:
  ```bash
  git clone https://github.com/cossee-org/i-connects.git ~/i-connects
  ```
- **Tarball upload** — locally run
  `git archive --format=tar.gz -o iconnects.tgz HEAD`, then use the Console
  SSH gear icon → **Upload file**. On the VM:
  ```bash
  mkdir -p ~/i-connects && tar xzf ~/iconnects.tgz -C ~/i-connects
  ```

Then:

```bash
cd ~/i-connects
cp .env.example .env
```

## 4. Configure `.env`

Generate a secret:

```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

`nano .env` and set:

```ini
DJANGO_SETTINGS_MODULE=config.settings.prod
DJANGO_SECRET_KEY=<paste the hex>
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=demo.i-connects.cossee.org

# Postgres
POSTGRES_DB=iconnects
POSTGRES_USER=iconnects
POSTGRES_PASSWORD=<another random string>
DATABASE_URL=postgres://iconnects:<same password>@db:5432/iconnects

# Redis / Celery
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/0

# Email — console backend writes mail to container logs (good for demo).
# Swap for SMTP / SES when real delivery is needed.
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=i-connects <noreply@demo.i-connects.cossee.org>
```

Notes pulled from `config/settings/prod.py`:

- `SECRET_KEY` is read directly from the environment — missing or empty
  values crash Django at startup.
- `STATICFILES_STORAGE = CompressedManifestStaticFilesStorage` means
  `collectstatic` **must** run before gunicorn starts. The prod override
  handles that.
- `SECURE_SSL_REDIRECT` defaults to `True` and respects the
  `X-Forwarded-Proto` header set by Caddy. Leave it alone.

## 5. Launch

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps
docker compose logs --tail=80 web caddy celery
```

What this does:

- `web` runs `migrate` + `collectstatic` + `gunicorn` on `:8000`, internal
  network only.
- `caddy` binds `:80` / `:443`, fetches a Let's Encrypt cert for
  `demo.i-connects.cossee.org` on first request, and reverse-proxies to
  `web:8000`. Cert + state persist in the `caddy_data` and `caddy_config`
  named volumes.
- `db` and `redis` are unreachable from the public internet (`ports: []`).
- `celery` and `celery-beat` start once `db` and `redis` are healthy.

Create an admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

Smoke tests:

```bash
curl -I https://demo.i-connects.cossee.org/         # expect 200 or 302
docker compose ps                                    # all services healthy
```

Then browse to `https://demo.i-connects.cossee.org/` and walk the golden
path in `FOR_JIM.md`.

## 6. Day-2 operations

**Deploy a new commit:**

```bash
cd ~/i-connects
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

Migrations and `collectstatic` re-run automatically on container start.

**Tail logs:**

```bash
docker compose logs -f web
docker compose logs -f celery celery-beat
docker compose logs -f caddy
```

**Database backup (one-off):**

```bash
mkdir -p ~/backups
docker compose exec -T db pg_dump -U iconnects iconnects \
  | gzip > ~/backups/db-$(date +%F).sql.gz
```

**Database backup (nightly cron):** `crontab -e`, add:

```cron
0 3 * * * cd ~/i-connects && docker compose exec -T db pg_dump -U iconnects iconnects | gzip > ~/backups/db-$(date +\%F).sql.gz
```

**Restore from a dump:**

```bash
gunzip -c ~/backups/db-YYYY-MM-DD.sql.gz \
  | docker compose exec -T db psql -U iconnects -d iconnects
```

**Disk snapshots (the real DR path):** Compute Engine → Snapshots →
**Schedule**, daily, 7-day retention, attach to the VM's boot disk. This is
faster to restore than rebuilding from a `pg_dump`.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Caddy logs `no such host` / `dns problem` | A-record not propagated | `dig +short demo.i-connects.cossee.org`; wait or fix DNS. |
| Browser: `ERR_CONNECTION_REFUSED` on :443 | Firewall blocks 443 | Add `tcp:443` to the VM's firewall rule. |
| Django: `DisallowedHost` | `DJANGO_ALLOWED_HOSTS` mismatch | Must contain `demo.i-connects.cossee.org` exactly. |
| Infinite redirect loop | `SECURE_SSL_REDIRECT=True` but Caddy not forwarding `X-Forwarded-Proto` | Caddy sets it by default — check you didn't replace the `reverse_proxy` directive. |
| `relation "..." does not exist` | Migrations didn't run | `docker compose exec web python manage.py migrate`. |
| Static files 404 | `collectstatic` didn't run | `docker compose exec web python manage.py collectstatic --noinput`. |
| Cert stuck on staging / rate-limited | Repeated failed issuance | `docker compose down && docker volume rm i-connects_caddy_data && docker compose up -d`. Only do this once you've fixed the underlying DNS / firewall issue. |

## 8. Tear-down

To stop the demo but keep data:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

To wipe everything (including the database):

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml down -v
```

To decommission the VM entirely, delete it in the Console (Compute Engine →
VM instances → ⋮ → Delete) and release the reserved IP if it's no longer
needed.
