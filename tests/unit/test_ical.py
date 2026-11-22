from datetime import date

import pytest

from app.domain.ical import CalendarError, parse_ics

AIRBNB = """BEGIN:VCALENDAR\r
PRODID:-//Airbnb Inc//Hosting Calendar 1.0//EN\r
VERSION:2.0\r
BEGIN:VEVENT\r
DTEND;VALUE=DATE:20270102\r
DTSTART;VALUE=DATE:20261228\r
UID:1418fb94e984-3d1a8c3f8f6e8b8a0e3b1c1b2f@airbnb.com\r
DESCRIPTION:Reservation URL: https://www.airbnb.com/hosting/reservations/details/HMABC\r
 123\r
SUMMARY:Reserved\r
END:VEVENT\r
BEGIN:VEVENT\r
DTSTART;VALUE=DATE:20270110\r
DTEND;VALUE=DATE:20270115\r
UID:outro@airbnb.com\r
SUMMARY:Airbnb (Not available)\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_airbnb_all_day_events_keep_exit_exclusive() -> None:
    events = parse_ics(AIRBNB)
    assert [(e.start, e.end) for e in events] == [
        (date(2026, 12, 28), date(2027, 1, 2)),
        (date(2027, 1, 10), date(2027, 1, 15)),
    ]
    assert events[0].uid.endswith("@airbnb.com")


def test_datetime_events_use_sao_paulo_dates() -> None:
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nUID:a\nDTSTART:20261228T170000Z\nDTEND:20270102T140000Z\n"
        "END:VEVENT\nBEGIN:VEVENT\nUID:b\nDTSTART;TZID=America/New_York:20261220T220000\n"
        "DTEND;TZID=America/New_York:20261222T090000\nEND:VEVENT\nEND:VCALENDAR\n"
    )
    a, b = parse_ics(text)
    assert (a.start, a.end) == (date(2026, 12, 28), date(2027, 1, 2))  # 14h a 11h locais
    assert (b.start, b.end) == (date(2026, 12, 21), date(2026, 12, 22))  # 00h a 11h em São Paulo


def test_cancelled_missing_end_and_same_day() -> None:
    text = (
        "BEGIN:VCALENDAR\n"
        "BEGIN:VEVENT\nUID:x\nSTATUS:CANCELLED\nDTSTART;VALUE=DATE:20261201\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:y\nDTSTART;VALUE=DATE:20261205\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:z\nDTSTART:20261210T100000\nDTEND:20261210T180000\nEND:VEVENT\n"
        "END:VCALENDAR\n"
    )
    y, z = parse_ics(text)
    assert (y.start, y.end) == (date(2026, 12, 5), date(2026, 12, 6))
    assert (z.start, z.end) == (date(2026, 12, 10), date(2026, 12, 11))


@pytest.mark.parametrize(
    "text", ["", "<html>login</html>", "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART:amanhã\nEND:VEVENT"]
)
def test_rejects_non_calendars(text: str) -> None:
    with pytest.raises(CalendarError):
        parse_ics(text)


def test_event_without_uid_gets_stable_id() -> None:
    text = (
        "BEGIN:VCALENDAR\nBEGIN:VEVENT\nDTSTART;VALUE=DATE:20261205\n"
        "DTEND;VALUE=DATE:20261207\nEND:VEVENT\nEND:VCALENDAR"
    )
    assert parse_ics(text)[0].uid == "sem-uid-2026-12-05-2026-12-07"
