from datetime import date

import pytest

from app.domain.reminder import parse_reply

TODAY = date(2026, 12, 20)
CODES = frozenset({"CASA12", "CASA03"})


def parse(text: str):  # type: ignore[no-untyped-def]
    return parse_reply(text, today=TODAY, codes=CODES)


@pytest.mark.parametrize("text", ["OK", "ok", " Ok! ", "ok."])
def test_ok_confirms(text: str) -> None:
    reply = parse(text)
    assert reply is not None and reply.confirm and not reply.blocks


def test_block_across_new_year_keeps_exit_night_free() -> None:
    reply = parse("casa12 28/12-02/01")
    assert reply is not None and not reply.confirm and not reply.problems
    [block] = reply.blocks
    assert (block.property_code, block.start_date, block.end_date) == (
        "CASA12",
        date(2026, 12, 28),
        date(2027, 1, 2),
    )


def test_several_blocks_and_ok_in_one_message() -> None:
    reply = parse("CASA12 28/12 a 02/01\nCASA03: 10/01/2027 até 15/01/2027; ok")
    assert reply is not None and reply.confirm and len(reply.blocks) == 2
    assert reply.blocks[1].start_date == date(2027, 1, 10)


def test_phone_en_dash_is_accepted() -> None:
    reply = parse("CASA12 28/12\u201302/01")  # o teclado do celular troca o hífen pelo travessão
    assert reply is not None and len(reply.blocks) == 1


def test_date_without_year_already_past_means_next_year() -> None:
    reply = parse("CASA03 05/01-08/01")
    assert reply is not None and reply.blocks[0].start_date == date(2027, 1, 5)


def test_today_is_allowed_and_one_night_works() -> None:
    reply = parse("CASA03 20/12-21/12")
    assert reply is not None and reply.blocks[0].start_date == TODAY


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("CASA99 28/12-02/01", "não encontrada"),
        ("CASA12 31/02-03/03", "data inválida"),
        ("CASA12 10/12/2026-12/12/2026", "já passou"),
        ("CASA12 28/12/2026-28/12/2026", "depois da entrada"),
        ("CASA12 01/01/2027-05/01/2028", "até 1 ano"),
    ],
)
def test_problems(text: str, problem: str) -> None:
    reply = parse(text)
    assert reply is not None and not reply.blocks and problem in reply.problems[0]


@pytest.mark.parametrize("text", ["1", "assumo", "bom dia", "a casa 12 está livre?", ""])
def test_other_messages_are_not_replies(text: str) -> None:
    assert parse(text) is None
