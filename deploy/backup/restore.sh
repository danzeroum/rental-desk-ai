#!/bin/sh
# Restaura um backup num banco separado, confere e (opcional) troca pelo banco em uso.
#   restore-backup [arquivo|latest|remote] [--swap]
# Sem --swap é um exercício (restore drill): restaura em <db>_restore, confere e apaga.
# Com --swap (make restore): o banco atual vira <db>_antes_<data> e o restaurado assume.
set -eu
# shellcheck disable=SC3040
set -o pipefail
: "${BACKUP_PASSPHRASE:?}" "${PGDATABASE:?}"
dir="${BACKUP_DIR:-/backups}"
source="${1:-latest}"
swap="${2:-}"
work="${PGDATABASE}_restore"
started="$(date +%s)"

case "${source}" in
  latest) file="$(find "${dir}" -name "${PGDATABASE}-*.dump.gpg" -type f | sort | tail -n 1)" ;;
  remote) file="$(offsite-sync --latest "${dir}")" ;;
  *) file="${source}" ;;
esac
[ -n "${file}" ] && [ -f "${file}" ] || { echo "backup não encontrado: ${source}" >&2; exit 1; }
echo "Restaurando $(basename "${file}") em ${work}"

psql -v ON_ERROR_STOP=1 -d postgres -qc "DROP DATABASE IF EXISTS \"${work}\""
psql -v ON_ERROR_STOP=1 -d postgres -qc "CREATE DATABASE \"${work}\" OWNER rentaldesk_owner"
# Permissões no nível do banco não vão no dump (iguais às do deploy/postgres/init-roles.sh).
psql -v ON_ERROR_STOP=1 -d postgres -qc "REVOKE ALL ON DATABASE \"${work}\" FROM PUBLIC; GRANT CONNECT, TEMPORARY ON DATABASE \"${work}\" TO rentaldesk_owner, rentaldesk_app, rentaldesk_ops"
gpg --batch --quiet --decrypt --passphrase-fd 3 "${file}" 3<<EOF_PASS \
  | pg_restore --exit-on-error --dbname "${work}"
${BACKUP_PASSPHRASE}
EOF_PASS

# Conferência: tabelas principais existem e a corrente do audit_log de cada imobiliária está íntegra.
broken="$(psql -v ON_ERROR_STOP=1 -d "${work}" -Atc \
  "SELECT count(*) FROM tenant t WHERE audit_log_verify(t.id) IS NOT NULL")"
summary="$(psql -v ON_ERROR_STOP=1 -d "${work}" -Atc \
  "SELECT (SELECT count(*) FROM tenant)||' imobiliárias, '||(SELECT count(*) FROM message)||' mensagens, '||(SELECT count(*) FROM audit_log)||' eventos de auditoria, migração '||(SELECT version_num FROM alembic_version)")"
[ "${broken}" = "0" ] || { echo "corrente de auditoria quebrada em ${broken} imobiliária(s)" >&2; exit 1; }
elapsed=$(( $(date +%s) - started ))
echo "Conferido: ${summary}. Tempo de restauração: ${elapsed} s."

if [ "${swap}" = "--swap" ]; then
  old="${PGDATABASE}_antes_$(date +%Y%m%d%H%M%S)"
  psql -v ON_ERROR_STOP=1 -d postgres -qc \
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = '${PGDATABASE}' AND pid <> pg_backend_pid()" >/dev/null
  psql -v ON_ERROR_STOP=1 -d postgres -qc "ALTER DATABASE \"${PGDATABASE}\" RENAME TO \"${old}\""
  psql -v ON_ERROR_STOP=1 -d postgres -qc "ALTER DATABASE \"${work}\" RENAME TO \"${PGDATABASE}\""
  echo "Banco restaurado em uso. O anterior ficou como ${old} (apague quando tudo estiver certo)."
else
  psql -v ON_ERROR_STOP=1 -d postgres -qc "DROP DATABASE \"${work}\""
  printf '{"event":"restauracao_exercicio_ok","seconds":%s}\n' "${elapsed}"
fi
