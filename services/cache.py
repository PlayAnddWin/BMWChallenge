"""Private, versioned draft artifacts. Authoritative identities/PM state stay in the writer."""
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from config import Settings
from contracts.intelligence import DTO, RequirementDraft
from services.common import EngineError, canonical


class CachedDraft(DTO):
    key: str
    model: str
    prompt_version: str
    generated_run_id: str
    generated_at: str
    draft: RequirementDraft
    cache_version: str = "draft-cache-v1"


class FileDraftCache:
    def __init__(self, directory: Path):
        self.directory = directory

    @classmethod
    def from_settings(cls, settings: Settings) -> "FileDraftCache":
        return cls(settings.cache_dir / "drafts")

    def _path(self, key: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", key):
            raise EngineError("cache key must be a SHA-256 digest")
        return self.directory / f"{key}.json"

    def get(self, key: str) -> CachedDraft | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            entry = CachedDraft.model_validate_json(path.read_text(encoding="utf-8"))
        except (ValidationError, OSError) as error:
            raise EngineError("cached draft is unreadable or invalid") from error
        if entry.key != key or entry.cache_version != "draft-cache-v1":
            raise EngineError("cached draft version/key mismatch")
        return entry

    def put(self, key: str, draft: RequirementDraft, *, model: str, prompt_version: str, run_id: str) -> None:
        path = self._path(key)
        entry = CachedDraft(key=key, model=model, prompt_version=prompt_version, generated_run_id=run_id,
                            generated_at=datetime.now(timezone.utc).isoformat(), draft=draft)
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.directory / f".{uuid4()}.tmp"
        try:
            # Cache artifacts can contain BMW quotes. Keep new directories/files owner-only.
            self.directory.chmod(0o700)
            with temporary.open("x", encoding="utf-8") as stream:
                temporary.chmod(0o600)
                stream.write(canonical(entry.model_dump()))
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
