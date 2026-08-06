from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Supplier:
    id: str
    name: str
    priority: int
    filename_pattern: str
    format: str
    currency: str
    mapping: dict[str, str]


@dataclass(frozen=True)
class Settings:
    base_dir: Path
    app_name: str
    database_path: Path
    incoming_dir: Path
    archive_dir: Path
    rejected_dir: Path
    scan_interval_seconds: float
    worker_batch_size: int
    max_file_size_mb: int
    max_job_attempts: int
    conflict_price_threshold_percent: float
    suppliers: tuple[Supplier, ...]


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def load_settings(path: str | Path | None = None) -> Settings:
    default_config = Path(__file__).resolve().parent.parent / "config.toml"
    config_path = Path(
        path or os.getenv("VENDORSYNC_CONFIG", str(default_config))
    ).resolve()
    if not config_path.exists():
        raise FileNotFoundError(config_path)

    with config_path.open("rb") as handle:
        data: dict[str, Any] = tomllib.load(handle)

    base = config_path.parent
    app = data.get("app", {})
    suppliers: list[Supplier] = []
    for raw in data.get("suppliers", []):
        pattern = str(raw["filename_pattern"])
        re.compile(pattern)
        feed_format = str(raw.get("format", "csv")).lower()
        if feed_format not in {"csv", "json"}:
            raise ValueError(f"Unsupported supplier format: {feed_format}")
        suppliers.append(
            Supplier(
                id=str(raw["id"]),
                name=str(raw["name"]),
                priority=int(raw.get("priority", 100)),
                filename_pattern=pattern,
                format=feed_format,
                currency=str(raw.get("currency", "USD")).upper(),
                mapping={
                    str(target): str(source)
                    for target, source in raw.get("mapping", {}).items()
                },
            )
        )

    if not suppliers:
        raise ValueError("At least one supplier must be configured")

    settings = Settings(
        base_dir=base,
        app_name=str(app.get("name", "VendorSync Hub")),
        database_path=_resolve(base, str(app.get("database_path", "data/vendorsync.db"))),
        incoming_dir=_resolve(base, str(app.get("incoming_dir", "data/incoming"))),
        archive_dir=_resolve(base, str(app.get("archive_dir", "data/archive"))),
        rejected_dir=_resolve(base, str(app.get("rejected_dir", "data/rejected"))),
        scan_interval_seconds=max(float(app.get("scan_interval_seconds", 1.0)), 0.2),
        worker_batch_size=max(int(app.get("worker_batch_size", 10)), 1),
        max_file_size_mb=max(int(app.get("max_file_size_mb", 20)), 1),
        max_job_attempts=max(int(app.get("max_job_attempts", 3)), 1),
        conflict_price_threshold_percent=max(
            float(app.get("conflict_price_threshold_percent", 20)), 0.0
        ),
        suppliers=tuple(suppliers),
    )

    for folder in (
        settings.incoming_dir,
        settings.archive_dir,
        settings.rejected_dir,
        settings.database_path.parent,
    ):
        folder.mkdir(parents=True, exist_ok=True)

    return settings
