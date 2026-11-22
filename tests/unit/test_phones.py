import pytest

from app.domain.phones import InvalidPhoneError, to_e164


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(11) 98888-7777", "+5511988887777"),
        ("11988887777", "+5511988887777"),
        ("1133334444", "+551133334444"),
        ("+55 11 98888-7777", "+5511988887777"),
        ("5511988887777", "+5511988887777"),
        ("+1 415 555 0100", "+14155550100"),
    ],
)
def test_normalizes(raw: str, expected: str) -> None:
    assert to_e164(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "123", "988887777", "(01) 98888-7777", "+12", "551198888777712"]
)
def test_rejects(raw: str) -> None:
    with pytest.raises(InvalidPhoneError):
        to_e164(raw)
