"""Persistent sync checkpoint and distributed file lock managers for incremental MongoDB sync."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_logger = logging.getLogger("chatbot.rag.sync_state")

_DEFAULT_LOCK_PATH = Path("data/sync.lock")
_DEFAULT_CHECKPOINT_PATH = Path("data/sync_checkpoints.json")
_DEFAULT_LOCK_TTL_SECONDS = 600


def _resolve_checkpoint_path(explicit: Path | None) -> Path:
    """Resolves checkpoint file path from explicit arg or SYNC_CHECKPOINT_PATH env var."""
    if explicit is not None:
        return explicit
    env_val = os.environ.get("SYNC_CHECKPOINT_PATH", "").strip()
    return Path(env_val) if env_val else _DEFAULT_CHECKPOINT_PATH


def _resolve_lock_path(explicit: Path | None) -> Path:
    """Resolves sync lock file path from explicit arg or SYNC_LOCK_PATH env var."""
    if explicit is not None:
        return explicit
    env_val = os.environ.get("SYNC_LOCK_PATH", "").strip()
    return Path(env_val) if env_val else _DEFAULT_LOCK_PATH


class SyncLockError(Exception):
    """Raised when an operation cannot acquire the sync lock due to an ongoing sync."""


@dataclass
class SyncLockManager:
    """Manages file-based mutual exclusion lock with TTL to prevent concurrent sync operations."""

    lock_file: Path | None = None
    ttl_seconds: int = _DEFAULT_LOCK_TTL_SECONDS
    owner_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    _is_acquired: bool = False

    def __post_init__(self) -> None:
        """Resolves lock file path lazily to respect SYNC_LOCK_PATH test isolation."""
        self.lock_file = _resolve_lock_path(self.lock_file)

    def is_locked(self) -> bool:
        """Returns True if the lock file exists and is currently valid and unexpired."""
        if not self.lock_file.exists():
            return False
        try:
            content = json.loads(self.lock_file.read_text(encoding="utf-8"))
            acquired_at = float(content.get("acquired_at", 0))
            lock_ttl = float(content.get("ttl_seconds", self.ttl_seconds))
            return (time.time() - acquired_at) < lock_ttl
        except Exception:
            return False

    def acquire(self) -> bool:
        """Acquires lock file atomically or raises SyncLockError if held by another active process."""
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner_id": self.owner_id,
            "acquired_at": time.time(),
            "ttl_seconds": self.ttl_seconds,
        }

        try:
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(payload))
            self._is_acquired = True
            return True
        except FileExistsError:
            pass

        try:
            data = json.loads(self.lock_file.read_text(encoding="utf-8"))
            acquired_at = float(data.get("acquired_at", 0))
            lock_ttl = float(data.get("ttl_seconds", self.ttl_seconds))
            age = time.time() - acquired_at
            if age < lock_ttl:
                raise SyncLockError(
                    f"Sync already running (owner={data.get('owner_id')}, age={age:.1f}s)"
                )
            _logger.warning("Overriding stale sync lock (age=%.1fs > ttl=%ds)", age, lock_ttl)
        except (json.JSONDecodeError, KeyError, ValueError):
            _logger.warning("Overriding unreadable or corrupted sync lock file.")

        self.lock_file.write_text(json.dumps(payload), encoding="utf-8")
        self._is_acquired = True
        return True

    def heartbeat(self) -> bool:
        """Refreshes lock timestamp to prevent TTL expiration during long-running sync operations."""
        if not self._is_acquired or not self.lock_file.exists():
            return False
        try:
            data = json.loads(self.lock_file.read_text(encoding="utf-8"))
            if data.get("owner_id") != self.owner_id:
                return False
            data["acquired_at"] = time.time()
            self.lock_file.write_text(json.dumps(data), encoding="utf-8")
            return True
        except Exception:
            return False

    def release(self) -> None:
        """Releases the lock file strictly if acquired and owned by this instance."""
        if not self._is_acquired or not self.lock_file.exists():
            self._is_acquired = False
            return
        try:
            data = json.loads(self.lock_file.read_text(encoding="utf-8"))
            if data.get("owner_id") == self.owner_id:
                self.lock_file.unlink(missing_ok=True)
        except Exception as exc:
            _logger.warning("Failed to cleanly remove lock file: %s", exc)
        finally:
            self._is_acquired = False

    def __enter__(self) -> SyncLockManager:
        """Context manager entry acquiring lock."""
        self.acquire()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit safely releasing lock."""
        self.release()


@dataclass
class CollectionCheckpoint:
    """Stores high-watermark timestamp, record hashes, and sync health metrics for a collection."""

    collection_name: str
    last_sync_timestamp: str | None = None
    last_sync_status: str = "success"
    total_records: int = 0
    total_chunks: int = 0
    record_hashes: dict[str, str] = field(default_factory=dict)


class CheckpointManager:
    """Persists and manages high-watermark timestamps and content hashes across sync runs."""

    def __init__(self, checkpoint_path: Path | None = None) -> None:
        """Initializes checkpoint manager with explicit or env-isolated persistence file path."""
        self.checkpoint_path = _resolve_checkpoint_path(checkpoint_path)
        self._checkpoints: dict[str, CollectionCheckpoint] = {}
        self.load()

    def load(self) -> dict[str, CollectionCheckpoint]:
        """Loads persistent checkpoints from JSON storage file or initializes empty registry."""
        if not self.checkpoint_path.exists():
            self._checkpoints = {}
            return self._checkpoints
        try:
            raw = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            self._checkpoints = {
                name: CollectionCheckpoint(**item) for name, item in raw.items() if isinstance(item, dict)
            }
        except Exception as exc:
            _logger.warning("Failed to parse checkpoint file %s: %s", self.checkpoint_path, exc)
            self._checkpoints = {}
        return self._checkpoints

    def save(self) -> None:
        """Atomically saves checkpoint registry to disk using a temporary file replacement."""
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.checkpoint_path.with_suffix(".tmp")
        data = {name: asdict(ckpt) for name, ckpt in self._checkpoints.items()}
        temp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        temp_path.replace(self.checkpoint_path)

    def get_checkpoint(self, collection_name: str) -> CollectionCheckpoint:
        """Returns existing collection checkpoint or creates a default empty checkpoint."""
        if collection_name not in self._checkpoints:
            self._checkpoints[collection_name] = CollectionCheckpoint(collection_name=collection_name)
        return self._checkpoints[collection_name]

    def get_watermark(self, collection_name: str) -> datetime | None:
        """Parses and returns last successful sync timestamp as a timezone-aware UTC datetime."""
        ckpt = self._checkpoints.get(collection_name)
        if not ckpt or not ckpt.last_sync_timestamp:
            return None
        try:
            dt = datetime.fromisoformat(ckpt.last_sync_timestamp)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    def update_checkpoint(
        self,
        collection_name: str,
        last_sync_time: datetime,
        record_hashes: dict[str, str],
        total_records: int,
        total_chunks: int,
        status: str = "success",
    ) -> None:
        """Updates collection checkpoint state with new timestamp and hashes and saves to disk."""
        iso_time = last_sync_time.astimezone(timezone.utc).isoformat()
        ckpt = CollectionCheckpoint(
            collection_name=collection_name,
            last_sync_timestamp=iso_time,
            last_sync_status=status,
            total_records=total_records,
            total_chunks=total_chunks,
            record_hashes=dict(record_hashes),
        )
        self._checkpoints[collection_name] = ckpt
        self.save()
