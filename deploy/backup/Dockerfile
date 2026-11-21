# syntax=docker/dockerfile:1.7
FROM postgres:16-alpine
# Segredo opcional "extra_ca": só para builds atrás de proxy corporativo com TLS próprio.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -f /run/secrets/extra_ca ]; then export SSL_CERT_FILE=/run/secrets/extra_ca; fi; \
    apk add --no-cache gnupg
COPY --chmod=0755 backup.sh /usr/local/bin/backup-now
COPY --chmod=0755 loop.sh /usr/local/bin/backup-loop
# Volume nomeado herda dono e permissão deste diretório na criação.
RUN install -d -o postgres -g postgres -m 0700 /backups /var/lib/postgresql/.gnupg
USER postgres
CMD ["backup-loop"]
