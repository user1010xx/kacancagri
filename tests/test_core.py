import pytest
from datetime import date

from invekto_client import (
    parse_command_dates,
    call_key,
    format_call_message,
    filter_by_department,
    parse_call_datetime,
    _fetch_from_conversation_report,
    _is_conversation_missed_call,
    _is_missed_call,
    _is_uncompleted,
    _match_department,
    _normalize_conversation_record,
    _normalize_phone,
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


def test_filter_by_department_exact():
    calls = [
        {"Queue": "Gelen Arama", "Phone": "1"},
        {"QueueName": "Satış", "Phone": "2"},
        {"Queue": "gelen arama ekibi", "Phone": "3"},
    ]
    filtered = filter_by_department(calls, "Gelen Arama")
    assert len(filtered) == 1
    assert filtered[0]["Phone"] == "1"


def test_filter_by_department_loose():
    calls = [
        {"Queue": "Gelen Arama", "Phone": "1"},
        {"Queue": "gelen arama ekibi", "Phone": "3"},
    ]
    filtered = filter_by_department(calls, "Gelen Arama", loose=True)
    assert len(filtered) == 2


def test_match_department_modes():
    assert _match_department("Gelen Arama", "Gelen Arama")
    assert not _match_department("gelen arama ekibi", "Gelen Arama")
    assert _match_department("gelen arama ekibi", "Gelen Arama", loose=True)


def test_normalize_phone():
    assert _normalize_phone("905551112233") == "5551112233"
    assert _normalize_phone("05551112233") == "5551112233"


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


def test_conversation_missed_call_detection():
    assert _is_conversation_missed_call({"Direction": "MISSCALL"})
    assert _is_conversation_missed_call({"Direction": "misscall"})
    assert not _is_conversation_missed_call({"Direction": "OUT"})


def test_normalize_conversation_record():
    raw = {
        "Direction": "MISSCALL",
        "Phone": "905301718596",
        "CreateDate": "2026-06-27T00:00:00",
        "CreateTime": "11:02:02",
        "Queue": "Gelen Arama",
        "CallID": "abc123",
        "IsCompleted": False,
    }
    normalized = _normalize_conversation_record(raw)
    assert normalized["ID"] == "abc123"
    assert normalized["Status"] == "2"
    assert normalized["ChekInTime"] == "11:02:02"

    key = call_key(normalized)
    assert "905301718596" in key
    assert "Gelen Arama" in key
    assert "27.06.2026" in key


def test_fetch_from_conversation_report_filters(monkeypatch):
    from datetime import date

    sample_records = [
        {
            "Direction": "MISSCALL",
            "Phone": "905301718596",
            "CreateDate": "2026-06-27T00:00:00",
            "CreateTime": "11:02:02",
            "Queue": "Gelen Arama",
            "CallID": "c1",
            "IsCompleted": False,
        },
        {
            "Direction": "MISSCALL",
            "Phone": "905551112233",
            "CreateDate": "2026-06-27T00:00:00",
            "CreateTime": "08:00:00",
            "Queue": "MESAI DIŞI",
            "CallID": "c2",
            "IsCompleted": False,
        },
        {
            "Direction": "OUT",
            "Phone": "905551112233",
            "CreateDate": "2026-06-27T00:00:00",
            "CreateTime": "09:00:00",
            "Queue": "Gelen Arama",
            "CallID": "c3",
            "IsCompleted": False,
        },
        {
            "Direction": "MISSCALL",
            "Phone": "905551112233",
            "CreateDate": "2026-06-27T00:00:00",
            "CreateTime": "10:00:00",
            "Queue": "Gelen Arama",
            "CallID": "c4",
            "IsCompleted": True,
        },
    ]

    def fake_request(*_args, **_kwargs):
        return sample_records

    monkeypatch.setattr("invekto_client._request_report", fake_request)

    today = date(2026, 6, 27)
    all_calls = _fetch_from_conversation_report(
        "12345678",
        today,
        today,
        department_name="Gelen Arama",
        uncompleted_only=False,
    )
    assert len(all_calls) == 2

    open_calls = _fetch_from_conversation_report(
        "12345678",
        today,
        today,
        department_name="Gelen Arama",
        uncompleted_only=True,
    )
    assert len(open_calls) == 1
    assert open_calls[0]["Phone"] == "905301718596"
