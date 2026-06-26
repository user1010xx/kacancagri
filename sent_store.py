import json
from datetime import date, datetime, timedelta
from pathlib import Path


class SentStore:
    def __init__(self, path: Path, *, max_age_days: int = 45) -> None:
        self.path = path
        self.max_age_days = max_age_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._keys = self._load()

    def _extract_date_from_key(self, key: str) -> date | None:
        # key format: "ID|Phone|dd.mm.yyyy|HH:MM:SS|dept"
        try:
            parts = key.split("|")
            if len(parts) >= 3:
                d = parts[2].strip()
                return datetime.strptime(d, "%d.%m.%Y").date()
        except Exception:
            pass
        return None

    def _cleanup_old(self, keys: set[str]) -> set[str]:
        if self.max_age_days <= 0:
            return keys
        cutoff = date.today() - timedelta(days=self.max_age_days)
        cleaned = set()
        for k in keys:
            kd = self._extract_date_from_key(k)
            if kd is None or kd >= cutoff:
                cleaned.add(k)
        return cleaned

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return set()

        keys = set(data)
        cleaned = self._cleanup_old(keys)
        if len(cleaned) != len(keys):
            # Persist cleaned version immediately
            self._keys = cleaned  # temp for save
            self._save()
            return cleaned
        return keys

    def _save(self) -> None:
        with self.path.open("w", encoding="utf-8") as file:
            json.dump(sorted(self._keys), file, ensure_ascii=False, indent=2)

    def has(self, key: str) -> bool:
        return key in self._keys

    def add(self, key: str) -> None:
        self._keys.add(key)
        self._save()

    def add_many(self, keys: list[str]) -> None:
        self._keys.update(keys)
        self._save()

    def count(self) -> int:
        return len(self._keys)

    def purge_old(self, days: int | None = None) -> int:
        """Force purge. Returns number of removed entries."""
        before = len(self._keys)
        self.max_age_days = days if days is not None else self.max_age_days
        self._keys = self._cleanup_old(self._keys)
        self._save()
        return before - len(self._keys)