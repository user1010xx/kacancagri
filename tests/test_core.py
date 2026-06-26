import pytest
from datetime import date

from invekto_client import (
    parse_command_dates,
    call_key,
    format_call_message,
    filter_by_department,
    parse_call_datetime,
    _is_missed_call,
    _is_uncompleted,
)


def test_parse_command_dates_valid():
    start, end = parse_command_dates("15.06.2026, 25.06.2026")
    assert start == date(2026, 6, 15)
    assert end == date(2026, 6, 25)


def test_parse_command_dates_invalid():
    with pytest.raises(ValueError):
        parse_command_dates("15.06.2026")


def test_parse_command_dates_reverse():
    with pytest.raises(ValueError):
        parse_command_dates("25.06.2026, 15.06.2026")


def test_call_key_and_format():
    sample = {
        "ID": "12345",
        "Phone": "905551112233",
        "ChekInDate": "2026-06-25",
        "ChekInTime": "14:22:11",
        "Queue": "Gelen Arama",
        "Status": "2",
    }
    key = call_key(sample)
    assert "12345" in key
    assert "905551112233" in key
    assert "25.06.2026" in key

    msg = format_call_message(sample)
    assert "Kaçan Çağrı" in msg
    assert "905551112233" in msg


def test_filter_by_department():
    calls = [
        {"Queue": "Gelen Arama", "Phone": "1"},
        {"QueueName": "Satış", "Phone": "2"},
        {"Queue": "gelen arama ekibi", "Phone": "3"},
    ]
    filtered = filter_by_department(calls, "Gelen Arama")
    assert len(filtered) == 2
    assert filtered[0]["Phone"] == "1"


def test_parse_call_datetime_variants():
    call1 = {"ChekInDate": "2026-06-25", "ChekInTime": "09:15:00"}
    call2 = {"CreateDate": "25.06.2026", "CreateTime": "10:05"}
    call3 = {"Date": "2026-06-25T11:30:00", "Time": "11:30"}

    d1 = parse_call_datetime(call1)
    d2 = parse_call_datetime(call2)
    d3 = parse_call_datetime(call3)

    assert d1 is not None
    assert d2 is not None
    assert d3 is not None
    assert d1.day == 25


def test_is_missed_and_uncompleted():
    assert _is_missed_call({"Status": "2"})
    assert not _is_missed_call({"Status": "1"})

    assert _is_uncompleted({"IsCompleted": False})
    assert _is_uncompleted({"IsCompleted": "0"})
    assert not _is_uncompleted({"IsCompleted": True})
