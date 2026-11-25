#!/bin/sh
# Gera um dump cifrado (AES-256, gpg simétrico) em /backups, envia a uma cópia externa
# S3 compatível (se configurada), apaga os antigos e registra o sinal de vida no Redis.
set -eu
# Sem pipefail, uma falha do pg_dump geraria um arquivo cifrado truncado e "válido".
# shellcheck disable=SC3040  # o sh do Alpine (busybox ash) suporta pipefail
set -o pipefail

: "${BACKUP_PASSPHRASE:?}" "${PGDATABASE:?}"
retention="${BACKUP_RETENTION_DAYS:-30}"
dir="${BACKUP_DIR:-/backups}"
stamp="$(date +%Y%m%dT%H%M%S)"
target="${dir}/${PGDATABASE}-${stamp}.dump.gpg"
partial="${target}.partial"

umask 077
trap 'rm -f -- "${partial}"' EXIT
# Com dono e permissões: a restauração recria tabelas com os papéis originais (RLS, GRANTs).
pg_dump --format=custom "${PGDATABASE}" \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-fd 3 --output "${partial}" 3<<EOF_PASS
${BACKUP_PASSPHRASE}
EOF_PASS
mv "${partial}" "${target}"
# Livro de eliminações (só ids internos): vai junto, para reaplicar depois de uma restauração.
ledger_src="${ERASURE_LEDGER:-/erasures/erasures.jsonl}"
ledger="${dir}/erasures.jsonl"
[ -f "${ledger_src}" ] && cp "${ledger_src}" "${ledger}"
find "${dir}" -name "${PGDATABASE}-*.dump.gpg" -type f -mtime +"${retention}" -delete
bytes="$(wc -c < "${target}")"

remote=false
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
  if offsite-sync "${target}" "${retention}" \
    && { [ ! -f "${ledger}" ] || offsite-sync --ledger "${ledger}"; }; then
    remote=true
  else
    printf '{"event":"backup_envio_externo_falhou","level":"error"}\n'
  fi
fi

if [ -n "${REDIS_URL:-}" ]; then
  redis-cli -u "${REDIS_URL}" --no-auth-warning SET rd:heartbeat:backup \
    "{\"at\":$(date +%s),\"bytes\":${bytes},\"remote\":${remote}}" >/dev/null \
    || printf '{"event":"backup_sinal_de_vida_falhou","level":"warning"}\n'
fi
printf '{"event":"backup_concluido","file":"%s","bytes":%s,"remote":%s}\n' \
  "$(basename "${target}")" "${bytes}" "${remote}"
if [ -n "${BACKUP_S3_BUCKET:-}" ] && [ "${remote}" = false ]; then
  exit 1  # cópia local feita, externa não: o loop registra a falha
fi
