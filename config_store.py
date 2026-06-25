import json
import os
from pathlib import Path
from typing import Any

DEFAULT_RUNTIME_CONFIG: dict[str, Any] = {
    "company_code": "",
}


class ConfigStore:
    def __init__(self, runtime_path: Path) -> None:
        self.runtime_path = runtime_path
        self.runtime_path.parent.mkdir(parents=True, exist_ok=True)
        self._runtime = self._load_runtime()
        self._load_env()

    def _load_env(self) -> None:
        self.department_name = os.getenv("INVEKTO_DEPARTMENT_NAME", "").strip()
        self.target_chat_id = self._read_chat_id()
        self.polling_interval_seconds = max(
            int(os.getenv("POLLING_INTERVAL_SECONDS", "30")),
            15,
        )
        self.notify_uncompleted_only = (
            os.getenv("NOTIFY_UNCOMPLETED_ONLY", "true").strip().lower()
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
    def _read_chat_id() -> int:
        raw = os.getenv("TELEGRAM_GROUP_CHAT_ID", "").strip()
        if not raw:
            return 0
        return int(raw)

    @property
    def company_code(self) -> str:
        return str(self._runtime.get("company_code", "")).strip()

    @company_code.setter
    def company_code(self, value: str) -> None:
        self._runtime["company_code"] = value.strip()
        self._save_runtime()

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
            errors.append("TELEGRAM_BOT_TOKEN")
        if not self.target_chat_id:
            errors.append("TELEGRAM_GROUP_CHAT_ID")
        return errors

    def as_text(self) -> str:
        department = self.department_name or "Tümü"
        company = self.company_code or "Ayarlanmadı (/firmakodu)"
        return (
            "⚙️ Bot Ayarları\n\n"
            f"🏢 Firma Kodu: {company}\n"
            f"🏷️ Kuyruk/Departman: {department}\n"
            f"💬 Bildirim Grubu: {self.target_chat_id or 'Tanımlı değil'}\n"
            f"⏱️ Kontrol Aralığı: {self.polling_interval_seconds} sn"
        )