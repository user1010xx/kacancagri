import json
from pathlib import Path


class SentStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._keys = self._load()

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()

        with self.path.open("r", encoding="utf-8") as file:
            data = json.load(file)

        if not isinstance(data, list):
            return set()
        return set(data)

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