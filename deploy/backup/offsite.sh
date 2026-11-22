#!/bin/sh
# Envia um backup para o armazenamento S3 compatível e apaga lá os mais velhos que a retenção.
# Uso: offsite-sync <arquivo> <dias>   |   offsite-sync --latest <dir-destino>
set -eu
: "${BACKUP_S3_BUCKET:?}" "${BACKUP_S3_ENDPOINT:?}" "${BACKUP_S3_ACCESS_KEY:?}" "${BACKUP_S3_SECRET_KEY:?}"
export RCLONE_CONFIG_OFFSITE_TYPE=s3
export RCLONE_CONFIG_OFFSITE_PROVIDER="${BACKUP_S3_PROVIDER:-Other}"
export RCLONE_CONFIG_OFFSITE_ENDPOINT="${BACKUP_S3_ENDPOINT}"
export RCLONE_CONFIG_OFFSITE_REGION="${BACKUP_S3_REGION:-us-east-1}"
export RCLONE_CONFIG_OFFSITE_ACCESS_KEY_ID="${BACKUP_S3_ACCESS_KEY}"
export RCLONE_CONFIG_OFFSITE_SECRET_ACCESS_KEY="${BACKUP_S3_SECRET_KEY}"
remote="offsite:${BACKUP_S3_BUCKET}/${BACKUP_S3_PREFIX:-rental-desk}"

if [ "${1:-}" = "--latest" ]; then
  name="$(rclone lsf --files-only "${remote}" | grep '\.dump\.gpg$' | sort | tail -n 1)"
  [ -n "${name}" ] || { echo "nenhum backup no armazenamento externo" >&2; exit 1; }
  rclone copyto "${remote}/${name}" "${2:?}/${name}"
  echo "${2}/${name}"
  exit 0
fi
rclone copyto --s3-no-check-bucket "${1:?}" "${remote}/$(basename "$1")"
rclone delete --min-age "${2:?}d" --include '*.dump.gpg' "${remote}"
