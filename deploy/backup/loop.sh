#!/bin/sh
# Executa backup-now uma vez por dia na hora BACKUP_HOUR (fuso do contêiner).
set -eu
hour="${BACKUP_HOUR:-03}"
last=""
while true; do
  today="$(date +%F)"
  if [ "$(date +%H)" = "${hour}" ] && [ "${last}" != "${today}" ]; then
    if backup-now; then
      last="${today}"
    else
      printf '{"event":"backup_falhou","level":"error"}\n'
    fi
  fi
  sleep 60
done
