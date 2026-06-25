from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from invekto_client import _call_datetime, _department_name


def export_missed_calls_excel(
    calls: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Kaçan Çağrılar"

    header_font = Font(name="Arial", bold=True)
    sheet["A1"] = "Telefon"
    sheet["A1"].font = header_font

    for index, call in enumerate(calls, start=2):
        sheet.cell(row=index, column=1, value=str(call.get("Phone") or ""))

    sheet.column_dimensions["A"].width = 18
    output_path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output_path)
    return output_path


def sort_calls(calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(call: dict[str, Any]) -> tuple[str, str]:
        call_date, call_time = _call_datetime(call)
        return call_date, call_time

    return sorted(calls, key=sort_key)