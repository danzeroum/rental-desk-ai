#!/bin/sh
# Gera um dump cifrado (AES-256, gpg simétrico) em /backups e apaga os antigos.
# O envio para armazenamento externo (S3 compatível) e o `make restore` entram no M8.
set -eu
# Sem pipefail, uma falha do pg_dump geraria um arquivo cifrado truncado e "válido".
# shellcheck disable=SC3040  # o sh do Alpine (busybox ash) suporta pipefail
set -o pipefail

: "${BACKUP_PASSPHRASE:?}" "${PGDATABASE:?}"
retention="${BACKUP_RETENTION_DAYS:-30}"
dir=/backups
stamp="$(date +%Y%m%dT%H%M%S)"
target="${dir}/${PGDATABASE}-${stamp}.dump.gpg"
partial="${target}.partial"

umask 077
trap 'rm -f -- "${partial}"' EXIT
pg_dump --format=custom --no-owner "${PGDATABASE}" \
  | gpg --batch --yes --symmetric --cipher-algo AES256 \
        --passphrase-fd 3 --output "${partial}" 3<<EOF_PASS
${BACKUP_PASSPHRASE}
EOF_PASS
mv "${partial}" "${target}"
find "${dir}" -name "${PGDATABASE}-*.dump.gpg" -type f -mtime +"${retention}" -delete
printf '{"event":"backup_concluido","file":"%s","bytes":%s}\n' \
  "$(basename "${target}")" "$(wc -c < "${target}")"
