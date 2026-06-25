import requests
from datetime import date, datetime
from typing import Any

API_URL = "https://app.invekto.com/invekto/pbxreport"
REPORT_TYPE_MISS_CALL = 2
REPORT_TYPE_QUEUE = 3
REPORT_TYPE_QUEUE_DETAIL = 4


class InvektoError(Exception):
    pass


def _parse_date(value: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Geçersiz tarih formatı: {value}")


def _match_department(name: str, target: str) -> bool:
    name_cf = name.strip().casefold()
    target_cf = target.strip().casefold()
    if not name_cf or not target_cf:
        return False
    return name_cf == target_cf or target_cf in name_cf or name_cf in target_cf


def _request_report(
    company_code: str,
    start_date: date,
    end_date: date,
    report_type: int,
    *,
    queue: str | None = None,
    uncompleted_only: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    payload: dict[str, Any] = {
        "filterType": 0,
        "companyCode": company_code,
        "startDate": start_date.strftime("%Y-%m-%d"),
        "endDate": end_date.strftime("%Y-%m-%d"),
        "reportType": report_type,
    }

    if queue:
        payload["queue"] = queue

    if report_type in (REPORT_TYPE_MISS_CALL, REPORT_TYPE_QUEUE_DETAIL):
        payload["unCompleted"] = uncompleted_only

    response = requests.post(API_URL, json=payload, timeout=timeout)
    response.raise_for_status()

    body = response.json()
    if not body.get("Status"):
        message = body.get("Message") or "Invekto API isteği başarısız."
        raise InvektoError(message)

    data = body.get("Data") or []
    if not isinstance(data, list):
        raise InvektoError("Invekto API beklenmeyen veri döndürdü.")

    return data


def _is_missed_call(record: dict[str, Any]) -> bool:
    return str(record.get("Status", "")).strip() in {"2"}


def _is_uncompleted(record: dict[str, Any]) -> bool:
    value = record.get("IsCompleted")
    if isinstance(value, bool):
        return not value
    if value is None:
        return True
    return str(value).strip().lower() in {"false", "0", ""}


def _department_name(record: dict[str, Any]) -> str:
    return str(record.get("Queue") or record.get("QueueName") or "").strip()


def _normalize_date(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if "T" in text:
        text = text.split("T", 1)[0]

    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(text, fmt)
            return parsed.strftime("%d.%m.%Y")
        except ValueError:
            continue

    return text


def _call_datetime(record: dict[str, Any]) -> tuple[str, str]:
    raw_date = (
        record.get("ChekInDate")
        or record.get("CreateDate")
        or record.get("Date")
        or ""
    )
    call_time = (
        record.get("ChekInTime")
        or record.get("CreateTime")
        or record.get("Time")
        or ""
    )
    return _normalize_date(raw_date), str(call_time).strip()


def resolve_queue_number(
    company_code: str,
    start_date: date,
    end_date: date,
    department_name: str,
    *,
    timeout: int = 30,
) -> str | None:
    queues = _request_report(
        company_code,
        start_date,
        end_date,
        REPORT_TYPE_QUEUE,
        timeout=timeout,
    )

    for queue in queues:
        queue_name = str(queue.get("QueueName") or queue.get("Queue") or "").strip()
        if _match_department(queue_name, department_name):
            for key in ("QUEUE", "Queue", "Queue1", "queue"):
                value = queue.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()

    return None


def filter_by_department(
    calls: list[dict[str, Any]],
    department_name: str | None,
) -> list[dict[str, Any]]:
    if not department_name:
        return calls

    return [
        call
        for call in calls
        if _match_department(_department_name(call), department_name)
    ]


def _fetch_from_queue_detail(
    company_code: str,
    start_date: date,
    end_date: date,
    queue_number: str,
    *,
    uncompleted_only: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    records = _request_report(
        company_code,
        start_date,
        end_date,
        REPORT_TYPE_QUEUE_DETAIL,
        queue=queue_number,
        uncompleted_only=uncompleted_only,
        timeout=timeout,
    )

    missed_calls = [record for record in records if _is_missed_call(record)]
    if uncompleted_only:
        missed_calls = [record for record in missed_calls if _is_uncompleted(record)]

    return missed_calls


def _fetch_from_miss_call_report(
    company_code: str,
    start_date: date,
    end_date: date,
    *,
    uncompleted_only: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    return _request_report(
        company_code,
        start_date,
        end_date,
        REPORT_TYPE_MISS_CALL,
        uncompleted_only=uncompleted_only,
        timeout=timeout,
    )


def fetch_missed_calls(
    company_code: str,
    start_date: date,
    end_date: date,
    *,
    department_name: str | None = None,
    uncompleted_only: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    if department_name:
        queue_number = resolve_queue_number(
            company_code,
            start_date,
            end_date,
            department_name,
            timeout=timeout,
        )
        if queue_number:
            return _fetch_from_queue_detail(
                company_code,
                start_date,
                end_date,
                queue_number,
                uncompleted_only=uncompleted_only,
                timeout=timeout,
            )

    calls = _fetch_from_miss_call_report(
        company_code,
        start_date,
        end_date,
        uncompleted_only=uncompleted_only,
        timeout=timeout,
    )
    return filter_by_department(calls, department_name)


def call_key(call: dict[str, Any]) -> str:
    call_date, call_time = _call_datetime(call)
    return "|".join(
        [
            str(call.get("ID", "")),
            str(call.get("Phone", "")),
            call_date,
            call_time,
            _department_name(call),
        ]
    )


def format_call_message(call: dict[str, Any]) -> str:
    phone = call.get("Phone") or "Bilinmiyor"
    call_date, call_time = _call_datetime(call)
    call_datetime = f"{call_date} {call_time}".strip() or "Bilinmiyor"
    department = _department_name(call) or "Bilinmiyor"

    return (
        "🔴 Kaçan Çağrı\n\n"
        f"📞 Telefon: {phone}\n"
        f"🕐 Arama Saati: {call_datetime}\n"
        f"🏷️ Departman: {department}"
    )


def get_available_queues(
    company_code: str,
    start_date: date,
    end_date: date,
    *,
    timeout: int = 30,
) -> list[tuple[str, str]]:
    queues: dict[str, str] = {}

    for report in _request_report(
        company_code, start_date, end_date, REPORT_TYPE_QUEUE, timeout=timeout
    ):
        name = str(report.get("QueueName") or report.get("Queue") or "").strip()
        number = str(report.get("QUEUE") or "").strip()
        if name:
            queues[name] = number

    for report in _request_report(
        company_code,
        start_date,
        end_date,
        REPORT_TYPE_MISS_CALL,
        uncompleted_only=False,
        timeout=timeout,
    ):
        name = _department_name(report)
        if name and name not in queues:
            queues[name] = ""

    return sorted((name, number) for name, number in queues.items())


def parse_command_dates(text: str) -> tuple[date, date]:
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) != 2:
        raise ValueError("Tarih aralığı virgülle ayrılmış iki tarih olmalı.")

    start = _parse_date(parts[0])
    end = _parse_date(parts[1])
    if start > end:
        raise ValueError("Başlangıç tarihi bitiş tarihinden büyük olamaz.")

    return start, end