from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from . import database as db
from .config import Settings
from .importer import Importer, PermanentImportError


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.importer = Importer(settings)
        # The background loop and a request can both ask for a run at once.
        # Serialising them means a caller that waits finds its job finished.
        self._lock = threading.Lock()

    def scan_incoming(self) -> int:
        """Queue feeds that were dropped into the incoming folder by hand.

        A file is picked up once it has stopped changing for `settle_seconds`,
        so a copy that is still in progress is never read half written.
        """
        folder = self.settings.incoming_dir
        if not folder.exists():
            return 0
        owned = db.known_job_paths(self.settings.database_path)
        queued = 0
        for path in sorted(folder.iterdir()):
            if not path.is_file() or path.suffix.lower() not in {".csv", ".json"}:
                continue
            if str(path.resolve()) in owned:
                continue
            if time.time() - path.stat().st_mtime < self.settings.settle_seconds:
                continue
            db.enqueue(
                self.settings.database_path,
                "import_feed",
                None,
                json.dumps({"path": str(path)}),
                self.settings.max_job_attempts,
            )
            db.audit(
                self.settings.database_path,
                "feed_found",
                "info",
                f"{path.name} found in the incoming folder",
            )
            queued += 1
        return queued

    def run_once(self) -> list[dict[str, Any]]:
        with self._lock:
            return self._run_once()

    def _run_once(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        db.recover_stale(
            self.settings.database_path, self.settings.job_timeout_seconds
        )
        for job in db.claim(
            self.settings.database_path,
            self.settings.worker_batch_size,
        ):
            job_id = int(job["id"])
            try:
                payload = json.loads(job["payload_json"])
                if job["job_type"] != "import_feed":
                    raise PermanentImportError(
                        f"Unsupported job type: {job['job_type']}"
                    )
                if not isinstance(payload, dict) or not payload.get("path"):
                    raise PermanentImportError("Job payload does not contain a file path")

                result = self.importer.process(
                    job_id,
                    Path(str(payload["path"])),
                    job.get("supplier_id"),
                )
                db.complete_job(self.settings.database_path, job_id)
                results.append(
                    {"job_id": job_id, "status": "completed", "result": result}
                )
            except PermanentImportError as exc:
                status = db.fail_job(
                    self.settings.database_path,
                    job_id,
                    str(exc),
                    int(job["attempts"]),
                    int(job["max_attempts"]),
                    retryable=False,
                )
                results.append(
                    {"job_id": job_id, "status": status, "error": str(exc)}
                )
            except Exception as exc:
                status = db.fail_job(
                    self.settings.database_path,
                    job_id,
                    str(exc),
                    int(job["attempts"]),
                    int(job["max_attempts"]),
                    retryable=True,
                )
                db.audit(
                    self.settings.database_path,
                    "job_failed",
                    "error",
                    f"Job #{job_id}: {exc}",
                )
                results.append(
                    {"job_id": job_id, "status": status, "error": str(exc)}
                )
        return results
