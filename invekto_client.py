import re
import requests
from datetime import date, datetime, timedelta
from typing import Any

API_URL = "https://app.invekto.com/invekto/pbxreport"
REPORT_TYPE_MISS_CALL = 2
REPORT_TYPE_QUEUE = 3
REPORT_TYPE_QUEUE_DETAIL = 4
REPORT_TYPE_CONVERSATION = 5


class InvektoError(Exception):
    pass


def _parse_date(value: str) -> date:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Geçersiz tarih formatı: {value}")


def _match_department(name: str, target: str, *, loose: bool = False) -> bool:
    name_cf = name.strip().casefold()
    target_cf = target.strip().casefold()
    if not name_cf or not target_cf:
        return False
    if name_cf == target_cf:
        return True
    if not loose:
        return False
    return target_cf in name_cf or name_cf in target_cf


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

    # Basit retry (3 deneme)
    for attempt in range(3):
        try:
            response = requests.post(API_URL, json=payload, timeout=timeout)
            response.raise_for_status()
            break
        except Exception:
            if attempt == 2:
                raise
            import time as _t
            _t.sleep(1.5 * (attempt + 1))

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


def parse_call_datetime(call: dict[str, Any]) -> datetime | None:
    """Parse call record to a real datetime object for reliable sorting and comparison."""
    try:
        date_str, time_str = _call_datetime(call)
        if not date_str:
            return None
        time_str = time_str or "00:00:00"
        for fmt in ("%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(f"{date_str} {time_str}".strip(), fmt)
            except ValueError:
                continue
        # Last attempt with raw
        raw_date = str(call.get("ChekInDate") or call.get("CreateDate") or call.get("Date") or "").strip()
        raw_time = str(call.get("ChekInTime") or call.get("CreateTime") or call.get("Time") or "").strip()
        if raw_date and "T" in raw_date:
            raw_date = raw_date.split("T", 1)[0]
        if raw_date and raw_time:
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    return datetime.strptime(f"{raw_date} {raw_time}", fmt)
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def resolve_queue_number(
    company_code: str,
    start_date: date,
    end_date: date,
    department_name: str,
    *,
    loose: bool = False,
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
        if _match_department(queue_name, department_name, loose=loose):
            for key in ("QUEUE", "Queue", "Queue1", "queue"):
                value = queue.get(key)
                if value is not None and str(value).strip():
                    return str(value).strip()

    return None


def filter_by_department(
    calls: list[dict[str, Any]],
    department_name: str | None,
    *,
    loose: bool = False,
) -> list[dict[str, Any]]:
    if not department_name:
        return calls

    return [
        call
        for call in calls
        if _match_department(_department_name(call), department_name, loose=loose)
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
    loose_department_match: bool = False,
    timeout: int = 30,
) -> list[dict[str, Any]]:
    if department_name:
        queue_number = resolve_queue_number(
            company_code,
            start_date,
            end_date,
            department_name,
            loose=loose_department_match,
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
    return filter_by_department(calls, department_name, loose=loose_department_match)


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


# ====================== YENİ: GÖRÜŞME + PERSONEL YÖNLENDİRME ======================

def _normalize_phone(phone: str) -> str:
    """Normalize telefon numarasını son 10 haneli core haline getirir.
    Örnek: 905551112233, 05551112233, 5551112233 → 5551112233
    """
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) > 10:
        if digits.startswith("90"):
            digits = digits[2:]
        elif digits.startswith("0"):
            digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


def _parse_conversation_datetime(date_value: Any, time_value: Any) -> datetime:
    text = f"{date_value} {time_value}".strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%d.%m.%Y %H:%M",
    ):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    return datetime.min


def _extract_dahili_from_record(rec: dict[str, Any]) -> str:
    return str(
        rec.get("ExtensionName")
        or rec.get("Extension")
        or rec.get("extensionName")
        or rec.get("extension")
        or ""
    ).strip()


def build_phone_dahili_cache(
    company_code: str,
    days: int = 15,
    timeout: int = 30,
) -> dict[str, str]:
    """Son N günlük görüşme raporundan telefon -> son dahili eşlemesi üretir."""
    end_date = date.today()
    start_date = end_date - timedelta(days=days)

    try:
        records = _request_report(
            company_code,
            start_date,
            end_date,
            REPORT_TYPE_CONVERSATION,
            timeout=timeout,
        )
    except Exception:
        return {}

    phone_best: dict[str, tuple[datetime, str]] = {}
    for rec in records:
        phone_key = _normalize_phone(rec.get("Phone") or rec.get("phone") or "")
        dahili = _extract_dahili_from_record(rec)
        if not phone_key or not dahili:
            continue

        when = _parse_conversation_datetime(
            rec.get("Date") or rec.get("ChekInDate") or rec.get("CreateDate") or "",
            rec.get("Time") or rec.get("ChekInTime") or rec.get("CreateTime") or "",
        )
        prev = phone_best.get(phone_key)
        if prev is None or when > prev[0]:
            phone_best[phone_key] = (when, dahili)

    return {phone: dahili for phone, (_, dahili) in phone_best.items()}


def get_last_dahili_for_phone(
    company_code: str,
    phone: str,
    days: int = 15,
    timeout: int = 30,
    *,
    cache: dict[str, str] | None = None,
) -> str | None:
    """Son N günde bu numara ile ilgili en son dahiliyi döndürür."""
    phone_key = _normalize_phone(phone)
    if not phone_key:
        return None

    if cache is not None:
        return cache.get(phone_key)

    single_cache = build_phone_dahili_cache(company_code, days=days, timeout=timeout)
    return single_cache.get(phone_key)