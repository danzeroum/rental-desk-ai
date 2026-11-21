import pytest

from app.core.db import async_url


@pytest.mark.parametrize(
    "url",
    ["postgresql://u:p@h/db", "postgres://u:p@h/db", "postgresql+psycopg://u:p@h/db"],
)
def test_async_url_uses_psycopg(url: str) -> None:
    assert async_url(url) == "postgresql+psycopg://u:p@h/db"


def test_async_url_rejects_other_databases() -> None:
    with pytest.raises(ValueError, match="postgresql"):
        async_url("mysql://u:p@h/db")
