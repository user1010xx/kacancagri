import json
import os
import threading
from datetime import date, datetime, timedelta
from pathlib import Path


class SentStore:
    def __init__(self, path: Path, *, max_age_days: int = 45) -> None:
        self.path = path
        self.max_age_days = max_age_days
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._completed, self._group_notified = self._load()
        self._dirty = False

    def _extract_date_from_key(self, key: str) -> date | None:
        try:
            parts = key.split("|")
            if len(parts) >= 3:
                d = parts[2].strip()
                return datetime.strptime(d, "%d.%m.%Y").date()
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_key_parts(key: str) -> tuple[str, str, str, str] | None:
        parts = key.split("|")
        if len(parts) < 5:
            return None
        return parts[1].strip(), parts[2].strip(), parts[3].strip(), parts[4].strip()

    @staticmethod
    def _time_to_seconds(time_str: str) -> int | None:
        for fmt in ("%H:%M:%S", "%H:%M"):
            try:
                parsed = datetime.strptime(time_str.strip(), fmt).time()
                return parsed.hour * 3600 + parsed.minute * 60 + parsed.second
            except ValueError:
                continue
        return None

    def _equivalent_key(self, left: str, right: str, *, max_time_delta: int = 120) -> bool:
        left_parts = self._parse_key_parts(left)
        right_parts = self._parse_key_parts(right)
        if not left_parts or not right_parts:
            return False

        left_phone, left_date, left_time, left_dept = left_parts
        right_phone, right_date, right_time, right_dept = right_parts
        if left_phone != right_phone or left_date != right_date or left_dept != right_dept:
            return False

        left_seconds = self._time_to_seconds(left_time)
        right_seconds = self._time_to_seconds(right_time)
        if left_seconds is None or right_seconds is None:
            return left_time == right_time
        return abs(left_seconds - right_seconds) <= max_time_delta

    def _find_equivalent_in(self, key: str, keys: set[str]) -> str | None:
        for existing in keys:
            if self._equivalent_key(key, existing):
                return existing
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

    def _load(self) -> tuple[set[str], set[str]]:
        if not self.path.exists():
            return set(), set()

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if isinstance(data, list):
            raw_completed = set(data)
            raw_group: set[str] = set()
            needs_save = True
        elif isinstance(data, dict):
            raw_completed = set(data.get("completed", []))
            raw_group = set(data.get("group_notified", []))
            needs_save = False
        else:
            return set(), set()

        completed = self._cleanup_old(raw_completed)
        group_notified = self._cleanup_old(raw_group)
        if len(completed) != len(raw_completed) or len(group_notified) != len(raw_group):
            needs_save = True

        if needs_save:
            self._completed = completed
            self._group_notified = group_notified
            self._dirty = True
            self._save()

        return completed, group_notified

    def _save(self) -> None:
        payload = {
            "completed": sorted(self._completed),
            "group_notified": sorted(self._group_notified),
        }
        temp_path = self.path.with_suffix(".tmp")
        with temp_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.path)
        self._dirty = False

    def flush(self) -> None:
        with self._lock:
            if self._dirty:
                self._save()

    def is_complete(self, key: str) -> bool:
        if key in self._completed:
            return True
        return self._find_equivalent_in(key, self._completed) is not None

    def is_group_notified(self, key: str) -> bool:
        if key in self._group_notified:
            return True
        return self._find_equivalent_in(key, self._group_notified) is not None

    def has(self, key: str) -> bool:
        return self.is_complete(key)

    def mark_group_notified(self, key: str, *, save: bool = True) -> None:
        with self._lock:
            self._group_notified.add(key)
            self._dirty = True
            if save:
                self._save()

    def mark_complete(self, key: str, *, save: bool = True) -> None:
        with self._lock:
            self._completed.add(key)
            self._group_notified.discard(key)
            self._dirty = True
            if save:
                self._save()

    def add(self, key: str) -> None:
        self.mark_complete(key)

    def add_many(self, keys: list[str], *, save: bool = True) -> None:
        with self._lock:
            for key in keys:
                if key in self._completed:
                    continue
                if self._find_equivalent_in(key, self._completed):
                    continue
                if self._find_equivalent_in(key, self._group_notified):
                    continue
                self._completed.add(key)
            self._dirty = True
            if save:
                self._save()

    def count(self) -> int:
        return len(self._completed)

    def group_notified_count(self) -> int:
        return len(self._group_notified)

    def purge_old(self, days: int | None = None) -> int:
        with self._lock:
            before = len(self._completed) + len(self._group_notified)
            if days is not None:
                self.max_age_days = days
            self._completed = self._cleanup_old(self._completed)
            self._group_notified = self._cleanup_old(self._group_notified)
            self._dirty = True
            self._save()
            after = len(self._completed) + len(self._group_notified)
            return before - after