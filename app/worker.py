from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import database as db
from .config import Settings
from .importer import Importer, PermanentImportError


class Worker:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.importer = Importer(settings)

    def run_once(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
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
