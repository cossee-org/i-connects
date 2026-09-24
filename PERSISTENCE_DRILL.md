# Persistence Drill — crash the stack, bring it back, prove nothing was lost

A manual, repeatable test that the production data volumes described in
`DEPLOYMENT.md` actually do their job. You plant known data, crash the stack
in three increasingly destructive ways, and after each one compare what is
there against what you planted.

| Volume | Mounted in | Holds | Must survive |
|---|---|---|---|
| `i-connects_postgres_data` | `db` → `/var/lib/postgresql/data` | users, projects, tasks, sessions, beat schedule | everything, including `down -v` |
| `i-connects_media_data` | `web`, `celery` → `/app/media` | `MEDIA_ROOT` | everything, including `down -v` |
| `i-connects_redis_data` | `redis` → `/data` | in-flight Celery queue | crashes and `down`, **not** `down -v` (by design) |

**Time:** about 30 minutes. **Downtime:** roughly 1–2 minutes per test while
containers restart. Run it in a quiet window and tell anyone using the site.

Run everything on `cossee-web-server` as `deploy`, from `~/i-connects`.

---

## 0. Prepare

### 0.1 Take a backup first

If anything goes wrong, this is what you restore from (`DEPLOYMENT.md`
Part K).

```bash
cd ~/i-connects
scripts/backup.sh
ls -lh ~/backups | tail -3          # note the db-*.dump and media-*.tar.gz you just made
```

### 0.2 Define helpers for this shell session

Paste this into your SSH session. The helpers only last for this session, so
paste them again if you reconnect (and again after the reboot in Test 3).

```bash
cd ~/i-connects
dc()  { docker compose -f docker-compose.yml -f docker-compose.prod.yml "$@"; }
# Run SQL from stdin inside the db container, using the container's own credentials.
sql() { dc exec -T db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At'; }
# Wait until every service reports (healthy). Gives up after 5 minutes.
wait_healthy() {
  for i in $(seq 1 60); do
    if [ "$(dc ps --format '{{.Health}}' | grep -vc '^healthy$')" = 0 ] && [ "$(dc ps -q | wc -l)" = 5 ]; then
      echo "all 5 services healthy"; dc ps; return 0
    fi
    sleep 5
  done
  echo "NOT healthy after 5 minutes:"; dc ps; return 1
}
# Everything that must survive, in one text blob we can diff.
fingerprint() {
  sql <<'SQL'
SELECT 'users=' || count(*) FROM accounts_user;
SELECT 'projects=' || count(*) FROM projects_project;
SELECT 'canary_rows=' || count(*) FROM zz_persistence_canary;
SELECT 'canary_last=' || id || ' ' || note FROM zz_persistence_canary ORDER BY id DESC LIMIT 1;
SQL
  echo "media(web)=$(dc exec -T web sha256sum /app/media/persistence-drill/canary.bin | cut -d' ' -f1)"
  echo "media(celery)=$(dc exec -T celery sha256sum /app/media/persistence-drill/canary.bin | cut -d' ' -f1)"
}
mkdir -p ~/drill
```

### 0.3 Confirm the stack is healthy and using the right volumes

```bash
wait_healthy
docker inspect -f '{{.Name}}{{range .Mounts}}  {{.Type}}:{{.Name}} -> {{.Destination}}{{end}}' $(dc ps -q)
```

You should see exactly these data mounts:

```
/i-connects-db-1      volume:i-connects_postgres_data -> /var/lib/postgresql/data
/i-connects-web-1     volume:i-connects_media_data -> /app/media
/i-connects-celery-1  volume:i-connects_media_data -> /app/media
/i-connects-redis-1   volume:i-connects_redis_data -> /data
```

**Stop here if** a data mount shows a long hex name (an anonymous volume) or
`bind:`. Data would not survive in that case, and the drill would only
confirm that. Fix the compose setup first.

### 0.4 Plant the canary data

**Database:** a small table of our own. Django migrations never touch tables
they don't know about, and Step 5 drops it.

```bash
sql <<'SQL'
CREATE TABLE IF NOT EXISTS zz_persistence_canary (
  id serial PRIMARY KEY,
  note text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO zz_persistence_canary (note) VALUES ('planted before drill');
SQL
```

**Media:** 1 MB of random bytes. A checksum on random data catches any
truncation or corruption, not just a missing file.

```bash
dc exec -T web sh -c 'mkdir -p /app/media/persistence-drill && head -c 1048576 /dev/urandom > /app/media/persistence-drill/canary.bin'
```

**Through the app, as a real user would:**

1. In a browser, log in to `https://i-connects.cossee.org/`.
2. Create a project named `Persistence drill 2026-09-24` (use today's
   date), or make any small edit you'll recognise.
3. **Leave this tab open and stay logged in.** Sessions live in Postgres, so
   still being logged in after each crash is itself a persistence check.

### 0.5 Record the baseline

```bash
fingerprint | tee ~/drill/baseline.txt
```

Expected shape (your numbers will differ):

```
users=12
projects=4
canary_rows=1
canary_last=1 planted before drill
media(web)=3f5a…c9
media(celery)=3f5a…c9
```

Both `media` lines must be identical, which confirms `web` and `celery`
share one volume.

---

## Test 1 — Hard crash (SIGKILL), automatic restart

**Simulates** an OOM kill, a segfault, or a process dying mid-write. Nothing
gets a chance to shut down cleanly, and nobody runs `docker compose` to
bring it back. The `restart: unless-stopped` policy has to do that alone.

**1.1 Make one last write immediately before the crash.** This checks that
a committed transaction survives even when Postgres is killed right
after it.

```bash
echo "INSERT INTO zz_persistence_canary (note) VALUES ('last write before crash 1');" | sql
fingerprint > ~/drill/before-1.txt
```

**1.2 Crash `db`, `web` and `celery` together** by SIGKILLing their main
processes from the host:

```bash
for s in db web celery; do
  pid=$(docker inspect -f '{{.State.Pid}}' "$(dc ps -q "$s")")
  echo "killing $s (host pid $pid)"; sudo kill -9 "$pid"
done
date -u +%H:%M:%S    # note the time
```

> Use `kill -9` on the host PID, not `docker kill`. Docker may treat
> `docker kill` as a manual stop, in which case `unless-stopped` will
> *not* restart the container. That would test the wrong thing.

**1.3 Do nothing, just watch.** Within a few seconds, Docker should restart
all three on its own:

```bash
wait_healthy
docker inspect -f '{{.Name}} restarts={{.RestartCount}} started={{.State.StartedAt}}' $(dc ps -q db web celery)
```

`RestartCount` should have gone up by 1 for each, and `StartedAt` should
be after the time you noted. `web` may log `database not reachable yet ...
retrying` a few times, and `celery` may restart once or twice while
Postgres recovers. Both are expected (`DEPLOYMENT.md` Part N).

**1.4 Confirm it was a real crash.** Postgres should report that it recovered
from an unclean shutdown:

```bash
dc logs db --since 10m | grep -E 'not properly shut down|automatic recovery|redo done'
```

If these lines are missing, the kill didn't hit Postgres, and the test
proved nothing about crash recovery. Check 1.2.

**1.5 Compare:**

```bash
fingerprint > ~/drill/after-1.txt
diff ~/drill/before-1.txt ~/drill/after-1.txt && echo "TEST 1 PASS: identical"
```

Also refresh the browser tab. You should still be logged in, and your
project should still be there.

---

## Test 2 — Containers destroyed and rebuilt

**Simulates** a redeploy, or a broken container that someone deletes and
recreates. The containers are thrown away entirely, so everything that
survives had to come from the volumes.

**2.1 Record the current container IDs, then tear down and rebuild:**

```bash
echo "INSERT INTO zz_persistence_canary (note) VALUES ('before test 2');" | sql
fingerprint > ~/drill/before-2.txt
dc ps -q | sort > ~/drill/ids-before-2.txt

dc down                      # removes all containers and the network (volumes stay)
docker ps -a --filter label=com.docker.compose.project=i-connects   # should list nothing
dc up -d --build
wait_healthy
```

**2.2 Prove the containers really are new, then compare the data:**

```bash
dc ps -q | sort > ~/drill/ids-after-2.txt
comm -12 ~/drill/ids-before-2.txt ~/drill/ids-after-2.txt | wc -l   # must print 0 (no container survived)

fingerprint > ~/drill/after-2.txt
diff ~/drill/before-2.txt ~/drill/after-2.txt && echo "TEST 2 PASS: identical"
```

Refresh the browser. You should still be logged in.

### 2b (optional) — `down -v` cannot delete the database

This proves the `external: true` safeguard. `down -v` **does** delete the
Redis volume, which holds any queued emails that haven't been sent yet. Only
run it if the queue is empty:

```bash
dc exec -T redis redis-cli llen celery      # must print 0; if not, wait and re-check, or skip 2b
```

```bash
fingerprint > ~/drill/before-2b.txt
dc down -v
docker volume ls | grep i-connects          # postgres_data and media_data still listed; redis_data gone
dc up -d
wait_healthy
fingerprint > ~/drill/after-2b.txt
diff ~/drill/before-2b.txt ~/drill/after-2b.txt && echo "TEST 2b PASS: identical"
```

---

## Test 3 — Whole-host restart (optional, coordinate first)

**Simulates** a power cut or kernel crash of the whole machine. Nobody logs
in afterwards: the stack has to start on its own at boot.

> **`cossee-web-server` is shared.** A reboot takes down every app on it,
> not just i-connects. Get agreement from whoever owns the other apps first,
> or skip this test. (`sudo systemctl restart docker` is a lighter version,
> but it still restarts every container on the host.)

**3.1 Before the reboot:**

```bash
dc ps                      # all 5 must be running. A stack you `stop`ped yourself will NOT come back at boot
echo "INSERT INTO zz_persistence_canary (note) VALUES ('before reboot');" | sql
fingerprint > ~/drill/before-3.txt
sudo reboot
```

**3.2 After the reboot:** wait about a minute, then SSH back in. **Do not
run `up`**, because the point is to see whether the stack came back by
itself. Paste the helpers from 0.2 again, then:

```bash
cd ~/i-connects
wait_healthy
fingerprint > ~/drill/after-3.txt
diff ~/drill/before-3.txt ~/drill/after-3.txt && echo "TEST 3 PASS: identical"
curl -sS https://i-connects.cossee.org/healthz/     # {"status": "ok", "database": "ok"} through Caddy + TLS
```

`celery`/`celery-beat` restarting a few times right after boot is expected
(`DEPLOYMENT.md` Part N).

---

## 4. Pass criteria

The drill passes when, for every test you ran:

- [ ] `diff` printed nothing and the `PASS` line appeared. Row counts, the
      last canary row and both media checksums are unchanged.
- [ ] The canary row written right before the SIGKILL (Test 1) is present.
- [ ] Postgres logged `automatic recovery` in Test 1, showing it was a real
      crash.
- [ ] `RestartCount` went up without anyone running a command (Test 1).
      The stack also came back without `up` after the reboot (Test 3).
- [ ] The browser session stayed logged in, and the project you created is
      still there.
- [ ] `wait_healthy` reported all 5 services healthy.

Each test's `canary_rows` is one higher than the last test's, because every
test adds a row before it runs. That is expected. The diff compares each
test's own before/after.

## If a check fails

- **Don't** run `docker volume create`, `down -v` or `restore.sh` in a hurry.
  First look at where the data went:
  `docker inspect -f '{{range .Mounts}}{{.Name}} -> {{.Destination}}{{"\n"}}{{end}}' $(dc ps -q db web)`.
  If `db` is mounting anything other than `i-connects_postgres_data`, the
  data is most likely still intact in the right volume. The container is
  simply looking at the wrong one.
- If a service won't turn healthy, work through `dc logs <service>` and
  `DEPLOYMENT.md` Part N.
- If data really is gone, restore from the backup made in 0.1:
  `scripts/restore.sh --db ~/backups/db-<ts>.dump --media ~/backups/media-<ts>.tar.gz --yes`.
  Then treat the failure as a bug in the compose setup.

## 5. Clean up

```bash
echo "DROP TABLE zz_persistence_canary;" | sql
dc exec -T web rm -rf /app/media/persistence-drill
rm -rf ~/drill
```

Delete the `Persistence drill …` project in the browser. Then record the
date, which tests you ran and the result, so the next person knows when
this was last checked.
