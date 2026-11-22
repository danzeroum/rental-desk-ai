"""Backup cifrado e restauração (M8): exercício de restauração e troca de banco.

Roda os scripts de verdade (`deploy/backup/*.sh`) contra um banco criado só para o
teste, com papéis, migrações e uma imobiliária com trilha de auditoria.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from tests.conftest import TEST_DATABASE_MIGRATION_URL

pytestmark = pytest.mark.integration
ADMIN_URL = os.environ.get(
    "TEST_DATABASE_ADMIN_URL", "postgresql://postgres:postgres@localhost:55432/postgres"
)
ROOT = Path(__file__).resolve().parents[2]
ROLE_PASSWORDS = {
    "DB_OWNER_PASSWORD": "owner_pw",
    "DB_APP_PASSWORD": "app_pw",
    "DB_OPS_PASSWORD": "ops_pw",
}
NEEDED = ("pg_dump", "pg_restore", "psql", "gpg", "bash")


def _pg_env(db: str, tmp: Path) -> dict[str, str]:
    admin = urlsplit(ADMIN_URL)
    env = {
        **os.environ,
        "PGHOST": admin.hostname or "localhost", "PGPORT": str(admin.port or 5432),
        "PGUSER": admin.username or "postgres", "PGPASSWORD": admin.password or "",
        "PGDATABASE": db, "BACKUP_DIR": str(tmp), "BACKUP_PASSPHRASE": "frase-do-teste-de-backup",
        "GNUPGHOME": str(tmp / "gnupg"),
    }  # fmt: skip
    env.pop("BACKUP_S3_BUCKET", None)
    env.pop("REDIS_URL", None)
    return env


def _run(script: str, env: dict[str, str], *args: str) -> str:
    result = subprocess.run(  # noqa: S603 - scripts do próprio repositório
        ["bash", str(ROOT / "deploy" / "backup" / script), *args],  # noqa: S607
        env=env, capture_output=True, text=True, timeout=300, check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr + result.stdout
    return result.stdout


def _url_for(url: str, db: str) -> str:
    return url.rsplit("/", 1)[0] + f"/{db}"


@pytest.fixture
def database(tmp_path: Path) -> Iterator[str]:
    if missing := [tool for tool in NEEDED if shutil.which(tool) is None]:
        pytest.skip(f"ferramentas ausentes: {missing}")
    db = f"rd_bk_{uuid.uuid4().hex[:8]}"
    env = _pg_env(db, tmp_path)
    (tmp_path / "gnupg").mkdir(mode=0o700)
    admin = create_engine(
        ADMIN_URL.replace("postgresql://", "postgresql+psycopg://"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{db}" OWNER rentaldesk_owner'))
    subprocess.run(  # noqa: S603
        [str(ROOT / "deploy" / "postgres" / "init-roles.sh")],
        env={**env, "POSTGRES_USER": env["PGUSER"], "POSTGRES_DB": db, **ROLE_PASSWORDS},
        check=True, capture_output=True,
    )  # fmt: skip
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("DATABASE_MIGRATION_URL", _url_for(TEST_DATABASE_MIGRATION_URL, db))
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield db
    with admin.connect() as conn:
        rows = conn.execute(
            text("SELECT datname FROM pg_database WHERE datname LIKE :p"), {"p": f"{db}%"}
        )
        for (name,) in list(rows):
            conn.execute(text(f'DROP DATABASE "{name}" WITH (FORCE)'))
    admin.dispose()


def _seed(db: str) -> uuid.UUID:
    tenant_id = uuid.uuid4()
    ops = create_engine(_url_for(ADMIN_URL, db).replace("postgresql://", "postgresql+psycopg://"))
    with ops.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenant (id, name, slug, status) VALUES (:id, 'Sol', :slug, 'active')"
            ),
            {"id": tenant_id, "slug": f"sol-{tenant_id.hex[:6]}"},
        )
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_id)}
        )
        for event in ("tenant_created", "settings_changed"):
            conn.execute(
                text(
                    "INSERT INTO audit_log (tenant_id, event_type, payload) VALUES (:t, :e, '{}')"
                ),
                {"t": tenant_id, "e": event},
            )
    ops.dispose()
    return tenant_id


def test_backup_drill_and_swap(database: str, tmp_path: Path) -> None:
    tenant_id = _seed(database)
    env = _pg_env(database, tmp_path)
    out = _run("backup.sh", env)
    assert '"event":"backup_concluido"' in out
    [dump] = list(tmp_path.glob(f"{database}-*.dump.gpg"))
    assert not dump.read_bytes().startswith(b"PGDMP")  # cifrado, não é o dump puro
    drill = _run("restore.sh", env, "latest")
    assert "Conferido: 1 imobiliárias" in drill and "restauracao_exercicio_ok" in drill
    swapped = _run("restore.sh", env, "latest", "--swap")
    assert "Banco restaurado em uso" in swapped
    app = create_engine(
        _url_for(
            "postgresql+psycopg://rentaldesk_app:app_pw@" + ADMIN_URL.split("@", 1)[1], database
        )
    )
    with app.begin() as conn:  # o papel da aplicação lê o banco restaurado, com RLS
        conn.execute(
            text("SELECT set_config('app.current_tenant_id', :t, true)"), {"t": str(tenant_id)}
        )
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 2
        conn.execute(text("SELECT set_config('app.current_tenant_id', '', true)"))
        assert conn.execute(text("SELECT count(*) FROM audit_log")).scalar_one() == 0
    app.dispose()


def test_wrong_passphrase_fails(database: str, tmp_path: Path) -> None:
    _seed(database)
    env = _pg_env(database, tmp_path)
    _run("backup.sh", env)
    result = subprocess.run(  # noqa: S603
        ["bash", str(ROOT / "deploy" / "backup" / "restore.sh"), "latest"],  # noqa: S607
        env={**env, "BACKUP_PASSPHRASE": "errada"}, capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert result.returncode != 0
