import json
import os
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "company_code": "",
    "backfilled_dates": [],
}


class ConfigStore:
    def __init__(self, runtime_path: Path) -> None:
        self.runtime_path = runtime_path
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self._runtime = self._load_runtime()
        self._load_env()

    def _load_env(self) -> None:
        raw_departments = os.getenv(
            "INVEKTO_DEPARTMENT_NAME",
            "Gelen Arama,MESAI DIŞI",
        ).strip()
        self.department_names = [
            part.strip() for part in raw_departments.split(",") if part.strip()
        ]
        self.department_name = ", ".join(self.department_names)
        self.target_chat_id, self._chat_id_error = self._read_chat_id()
        self.polling_interval_seconds = max(
            int(os.getenv("POLLING_INTERVAL_SECONDS", "30")),
            15,
        )
        self.notify_uncompleted_only = (
            os.getenv("NOTIFY_UNCOMPLETED_ONLY", "true").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        self.department_loose_match = (
            os.getenv("INVEKTO_DEPARTMENT_LOOSE_MATCH", "false").strip().lower()
            in {"1", "true", "yes", "on"}
        )

    def _load_runtime(self) -> dict[str, Any]:
        if not self.runtime_path.exists():
            return DEFAULT_RUNTIME_CONFIG.copy()

        with self.runtime_path.open("r", encoding="utf-8") as file:
            loaded = json.load(file)

        merged = DEFAULT_RUNTIME_CONFIG.copy()
        merged.update(loaded)
        return merged

    def _save_runtime(self) -> None:
        with self.runtime_path.open("w", encoding="utf-8") as file:
            json.dump(self._runtime, file, ensure_ascii=False, indent=2)

    @staticmethod
    def _read_chat_id() -> tuple[int, str | None]:
        raw = os.getenv("TELEGRAM_GROUP_CHAT_ID", "").strip().strip("\"'")
        if not raw:
            return 0, None
        try:
            return int(raw), None
        except ValueError:
            return 0, "geçersiz sayı"

    @property
    def company_code(self) -> str:
        return str(self._runtime.get("company_code", "")).strip()

    @company_code.setter
    def company_code(self, value: str) -> None:
        self._runtime["company_code"] = value.strip()
        self._save_runtime()

    @staticmethod
    def backfill_job_key(target: date, after_time: str | None = None) -> str:
        if after_time:
            return f"{target.isoformat()}|{after_time}"
        return target.isoformat()

    def is_backfilled(self, target: date, after_time: str | None = None) -> bool:
        key = self.backfill_job_key(target, after_time)
        stored = self._runtime.get("backfilled_dates", [])
        return isinstance(stored, list) and key in stored

    def mark_backfilled(self, target: date, after_time: str | None = None) -> None:
        stored = list(self._runtime.get("backfilled_dates", []))
        key = self.backfill_job_key(target, after_time)
        if key not in stored:
            stored.append(key)
            self._runtime["backfilled_dates"] = stored
            self._save_runtime()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
            errors.append("TELEGRAM_BOT_TOKEN")
        if self._chat_id_error:
            errors.append("TELEGRAM_GROUP_CHAT_ID (geçersiz)")
        elif not self.target_chat_id:
            errors.append("TELEGRAM_GROUP_CHAT_ID")
        return errors

    def as_text(self) -> str:
        department = self.department_name or "Tümü (filtre yok)"
        company = self.company_code or "Ayarlanmadı (/firmakodu)"
        notify_mode = (
            "Sadece tamamlanmamış" if self.notify_uncompleted_only else "Tümü"
        )
        dept_match = "Gevşek (substring)" if self.department_loose_match else "Tam eşleşme"
        return (
            "⚙️ Bot Ayarları\n\n"
            f"🏢 Firma Kodu: {company}\n"
            f"🏷️ Kuyruk/Departman: {department}\n"
            f"🔎 Departman eşleştirme: {dept_match}\n"
            f"📨 Bildirim filtresi: {notify_mode}\n"
            f"💬 Bildirim Grubu: {self.target_chat_id or 'Tanımlı değil'}\n"
            f"⏱️ Kontrol Aralığı: {self.polling_interval_seconds} sn"
        )