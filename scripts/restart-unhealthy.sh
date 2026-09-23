#!/usr/bin/env bash
# Restart every container of this compose project whose health check is
# failing.
#
# Docker never restarts a container because it is unhealthy: health status
# only shows in `docker ps` and gates `depends_on`, and `restart:
# unless-stopped` acts on process exit alone. This closes that gap without
# handing the Docker socket to a third-party autoheal image.
#
# Cron, every two minutes (DEPLOYMENT.md Part L):
#
#   */2 * * * * cd ~/i-connects && scripts/restart-unhealthy.sh >> ~/logs/restart-unhealthy.log 2>&1
#
# Prints one line per restart and nothing when all is well, so the log stays
# readable. COMPOSE_PROJECT_NAME overrides the project (default: i-connects,
# the `name:` in docker-compose.yml).
set -euo pipefail

PROJECT="${COMPOSE_PROJECT_NAME:-i-connects}"

mapfile -t unhealthy < <(
  docker ps --quiet \
    --filter "health=unhealthy" \
    --filter "label=com.docker.compose.project=$PROJECT"
)

[[ ${#unhealthy[@]} -eq 0 ]] && exit 0

for id in "${unhealthy[@]}"; do
  name="$(docker inspect --format '{{.Name}}' "$id" | sed 's#^/##')"
  printf '%s restart-unhealthy: restarting %s\n' "$(date -Is)" "$name"
  # `docker restart` honours the container's stop_grace_period / stop_signal.
  docker restart "$id" > /dev/null
done
