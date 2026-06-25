import os
from typing import Any


class ConfigStore:
    def __init__(self) -> None:
        self._reload()

    def _reload(self) -> None:
        self.company_code = os.getenv("INVEKTO_COMPANY_CODE", "").strip()
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

    @staticmethod
    def _read_chat_id() -> int:
        raw = os.getenv("TELEGRAM_GROUP_CHAT_ID", "").strip()
        if not raw:
            return 0
        return int(raw)

    def validate(self) -> list[str]:
        errors: list[str] = []
        if not os.getenv("TELEGRAM_BOT_TOKEN", "").strip():
            errors.append("TELEGRAM_BOT_TOKEN")
        if not self.target_chat_id:
            errors.append("TELEGRAM_GROUP_CHAT_ID")
        if not self.company_code:
            errors.append("INVEKTO_COMPANY_CODE")
        if self.company_code and (not self.company_code.isdigit() or len(self.company_code) != 8):
            errors.append("INVEKTO_COMPANY_CODE (8 haneli olmalı)")
        return errors

    def as_text(self) -> str:
        department = self.department_name or "Tümü"
        return (
            "⚙️ Bot Ayarları\n\n"
            f"🏢 Firma Kodu: {self.company_code or 'Tanımlı değil'}\n"
            f"🏷️ Kuyruk/Departman: {department}\n"
            f"💬 Bildirim Grubu: {self.target_chat_id or 'Tanımlı değil'}\n"
            f"⏱️ Kontrol Aralığı: {self.polling_interval_seconds} sn"
        )