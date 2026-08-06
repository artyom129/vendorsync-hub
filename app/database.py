from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS suppliers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    priority INTEGER NOT NULL,
    format TEXT NOT NULL,
    filename_pattern TEXT NOT NULL,
    currency TEXT NOT NULL,
    mapping_json TEXT NOT NULL,
    last_success_at TEXT
);

CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_type TEXT NOT NULL,
    supplier_id TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_run_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_ready ON jobs(status, next_run_at);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER,
    supplier_id TEXT,
    filename TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    status TEXT NOT NULL,
    total_rows INTEGER NOT NULL DEFAULT 0,
    valid_rows INTEGER NOT NULL DEFAULT 0,
    invalid_rows INTEGER NOT NULL DEFAULT 0,
    archive_path TEXT,
    message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    FOREIGN KEY (job_id) REFERENCES jobs(id),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_import_fingerprint ON imports(fingerprint);

CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL,
    row_number INTEGER,
    field_name TEXT,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    raw_value TEXT,
    FOREIGN KEY (import_id) REFERENCES imports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier_id TEXT NOT NULL,
    import_id INTEGER NOT NULL,
    version_number INTEGER NOT NULL,
    product_count INTEGER NOT NULL,
    new_items INTEGER NOT NULL,
    changed_items INTEGER NOT NULL,
    unchanged_items INTEGER NOT NULL,
    removed_items INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(supplier_id, version_number),
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id),
    FOREIGN KEY (import_id) REFERENCES imports(id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    supplier_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    currency TEXT NOT NULL,
    price TEXT NOT NULL,
    stock INTEGER NOT NULL,
    category TEXT NOT NULL,
    source_file TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    record_hash TEXT NOT NULL,
    FOREIGN KEY (version_id) REFERENCES versions(id) ON DELETE CASCADE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_version ON snapshots(version_id);
CREATE INDEX IF NOT EXISTS idx_snapshots_sku ON snapshots(supplier_id, sku);

CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id INTEGER NOT NULL,
    supplier_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    change_type TEXT NOT NULL,
    field_name TEXT,
    old_value TEXT,
    new_value TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (version_id) REFERENCES versions(id) ON DELETE CASCADE,
    FOREIGN KEY (supplier_id) REFERENCES suppliers(id)
);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(db: str | Path) -> sqlite3.Connection:
    path = Path(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 20000")
    return conn


def init_db(db: str | Path) -> None:
    with connect(db) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def upsert_supplier(db: str | Path, supplier: dict[str, Any]) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO suppliers (
                id, name, priority, format, filename_pattern, currency, mapping_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                priority = excluded.priority,
                format = excluded.format,
                filename_pattern = excluded.filename_pattern,
                currency = excluded.currency,
                mapping_json = excluded.mapping_json
            """,
            (
                supplier["id"],
                supplier["name"],
                supplier["priority"],
                supplier["format"],
                supplier["filename_pattern"],
                supplier["currency"],
                supplier["mapping_json"],
            ),
        )


def suppliers(db: str | Path) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM suppliers ORDER BY priority, name"
        ).fetchall()
    return [dict(row) for row in rows]


def enqueue(
    db: str | Path,
    job_type: str,
    supplier_id: str | None,
    payload_json: str,
    max_attempts: int,
) -> int:
    created = now()
    with connect(db) as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs (
                job_type, supplier_id, payload_json, status, attempts,
                max_attempts, next_run_at, created_at
            ) VALUES (?, ?, ?, 'pending', 0, ?, ?, ?)
            """,
            (job_type, supplier_id, payload_json, max_attempts, created, created),
        )
        return int(cursor.lastrowid)


def claim(db: str | Path, limit: int = 10) -> list[dict[str, Any]]:
    claimed: list[dict[str, Any]] = []
    current = now()
    with connect(db) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT id FROM jobs
            WHERE status IN ('pending', 'retrying') AND next_run_at <= ?
            ORDER BY id
            LIMIT ?
            """,
            (current, limit),
        ).fetchall()
        for row in rows:
            cursor = conn.execute(
                """
                UPDATE jobs
                SET status = 'running', attempts = attempts + 1,
                    started_at = ?, completed_at = NULL
                WHERE id = ? AND status IN ('pending', 'retrying')
                """,
                (current, row["id"]),
            )
            if cursor.rowcount:
                fresh = conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                claimed.append(dict(fresh))
        conn.commit()
    return claimed


def complete_job(db: str | Path, job_id: int) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = 'completed', completed_at = ?, last_error = NULL
            WHERE id = ?
            """,
            (now(), job_id),
        )


def fail_job(
    db: str | Path,
    job_id: int,
    error: str,
    attempts: int,
    max_attempts: int,
    *,
    retryable: bool = True,
) -> str:
    can_retry = retryable and attempts < max_attempts
    status = "retrying" if can_retry else "dead"
    if can_retry:
        delay_seconds = min(2 ** max(attempts - 1, 0), 30)
        next_run = (
            datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        ).replace(microsecond=0).isoformat()
    else:
        next_run = now()

    with connect(db) as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, next_run_at = ?, last_error = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                status,
                next_run,
                error[:2000],
                now() if status == "dead" else None,
                job_id,
            ),
        )
    return status


def retry(db: str | Path, job_id: int) -> bool:
    with connect(db) as conn:
        cursor = conn.execute(
            """
            UPDATE jobs
            SET status = 'pending', attempts = 0, next_run_at = ?,
                last_error = NULL, started_at = NULL, completed_at = NULL
            WHERE id = ? AND status IN ('dead', 'retrying')
            """,
            (now(), job_id),
        )
    return cursor.rowcount > 0


def update_job_payload(db: str | Path, job_id: int, payload_json: str) -> None:
    with connect(db) as conn:
        conn.execute(
            "UPDATE jobs SET payload_json = ? WHERE id = ?",
            (payload_json, job_id),
        )


def jobs(
    db: str | Path,
    status: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    with connect(db) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(row) for row in rows]


def get_job(db: str | Path, job_id: int) -> dict[str, Any] | None:
    with connect(db) as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return dict(row) if row else None


def fingerprint_exists(db: str | Path, fingerprint: str) -> bool:
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM imports
            WHERE fingerprint = ? AND status = 'accepted'
            LIMIT 1
            """,
            (fingerprint,),
        ).fetchone()
    return bool(row)


def create_import(
    db: str | Path,
    job_id: int,
    supplier_id: str | None,
    filename: str,
    fingerprint: str,
) -> int:
    with connect(db) as conn:
        cursor = conn.execute(
            """
            INSERT INTO imports (
                job_id, supplier_id, filename, fingerprint, status, created_at
            ) VALUES (?, ?, ?, ?, 'processing', ?)
            """,
            (job_id, supplier_id, filename, fingerprint, now()),
        )
        return int(cursor.lastrowid)


def finish_import(
    db: str | Path,
    import_id: int,
    status: str,
    total: int,
    valid: int,
    invalid: int,
    path: str,
    message: str,
) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            UPDATE imports
            SET status = ?, total_rows = ?, valid_rows = ?, invalid_rows = ?,
                archive_path = ?, message = ?, completed_at = ?
            WHERE id = ?
            """,
            (status, total, valid, invalid, path, message, now(), import_id),
        )


def add_issues(
    db: str | Path,
    import_id: int,
    items: list[dict[str, Any]],
) -> None:
    if not items:
        return
    with connect(db) as conn:
        conn.executemany(
            """
            INSERT INTO issues (
                import_id, row_number, field_name, code, message, raw_value
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    import_id,
                    item.get("row_number"),
                    item.get("field_name"),
                    item["code"],
                    item["message"],
                    item.get("raw_value"),
                )
                for item in items
            ],
        )


def latest_version(db: str | Path, supplier_id: str) -> dict[str, Any] | None:
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT * FROM versions
            WHERE supplier_id = ?
            ORDER BY version_number DESC
            LIMIT 1
            """,
            (supplier_id,),
        ).fetchone()
    return dict(row) if row else None


def version_rows(db: str | Path, version_id: int) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE version_id = ? ORDER BY sku",
            (version_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def next_version(db: str | Path, supplier_id: str) -> int:
    with connect(db) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(MAX(version_number), 0) + 1 AS n
            FROM versions WHERE supplier_id = ?
            """,
            (supplier_id,),
        ).fetchone()
    return int(row["n"])


def save_catalog_version(
    db: str | Path,
    *,
    supplier_id: str,
    import_id: int,
    number: int,
    products: list[dict[str, Any]],
    summary: dict[str, int],
    changes: list[dict[str, Any]],
) -> int:
    created = now()
    with connect(db) as conn:
        cursor = conn.execute(
            """
            INSERT INTO versions (
                supplier_id, import_id, version_number, product_count,
                new_items, changed_items, unchanged_items, removed_items,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                supplier_id,
                import_id,
                number,
                len(products),
                summary["new_items"],
                summary["changed_items"],
                summary["unchanged_items"],
                summary["removed_items"],
                created,
            ),
        )
        version_id = int(cursor.lastrowid)
        conn.executemany(
            """
            INSERT INTO snapshots (
                version_id, supplier_id, sku, name, description, currency,
                price, stock, category, source_file, source_row, record_hash
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    version_id,
                    item["supplier_id"],
                    item["sku"],
                    item["name"],
                    item["description"],
                    item["currency"],
                    item["price"],
                    item["stock"],
                    item["category"],
                    item["source_file"],
                    item["source_row"],
                    item["record_hash"],
                )
                for item in products
            ],
        )
        if changes:
            conn.executemany(
                """
                INSERT INTO changes (
                    version_id, supplier_id, sku, change_type, field_name,
                    old_value, new_value, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        version_id,
                        supplier_id,
                        item["sku"],
                        item["change_type"],
                        item.get("field_name"),
                        item.get("old_value"),
                        item.get("new_value"),
                        created,
                    )
                    for item in changes
                ],
            )
        conn.execute(
            "UPDATE suppliers SET last_success_at = ? WHERE id = ?",
            (created, supplier_id),
        )
        return version_id


def audit(db: str | Path, event_type: str, severity: str, message: str) -> None:
    with connect(db) as conn:
        conn.execute(
            """
            INSERT INTO audit (event_type, severity, message, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (event_type, severity, message, now()),
        )


def audit_events(db: str | Path, limit: int = 15) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(row) for row in rows]


def latest_snapshots(db: str | Path) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            """
            WITH latest_versions AS (
                SELECT supplier_id, MAX(version_number) AS version_number
                FROM versions
                GROUP BY supplier_id
            )
            SELECT p.*, s.name AS supplier_name,
                   s.priority AS supplier_priority
            FROM snapshots p
            JOIN versions v ON v.id = p.version_id
            JOIN latest_versions lv
              ON lv.supplier_id = v.supplier_id
             AND lv.version_number = v.version_number
            JOIN suppliers s ON s.id = p.supplier_id
            ORDER BY p.sku, s.priority, s.name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def versions(db: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            """
            SELECT v.*, s.name AS supplier_name
            FROM versions v
            JOIN suppliers s ON s.id = v.supplier_id
            ORDER BY v.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def changes(db: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            """
            SELECT c.*, s.name AS supplier_name
            FROM changes c
            JOIN suppliers s ON s.id = c.supplier_id
            ORDER BY c.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def imports(db: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    with connect(db) as conn:
        rows = conn.execute(
            """
            SELECT i.*, s.name AS supplier_name
            FROM imports i
            LEFT JOIN suppliers s ON s.id = i.supplier_id
            ORDER BY i.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def stats(db: str | Path) -> dict[str, int]:
    with connect(db) as conn:
        supplier_count = conn.execute(
            "SELECT COUNT(*) AS n FROM suppliers"
        ).fetchone()["n"]
        pending = conn.execute(
            """
            SELECT COUNT(*) AS n FROM jobs
            WHERE status IN ('pending', 'running', 'retrying')
            """
        ).fetchone()["n"]
        dead = conn.execute(
            "SELECT COUNT(*) AS n FROM jobs WHERE status = 'dead'"
        ).fetchone()["n"]
        version_count = conn.execute(
            "SELECT COUNT(*) AS n FROM versions"
        ).fetchone()["n"]
        import_count = conn.execute(
            "SELECT COUNT(*) AS n FROM imports"
        ).fetchone()["n"]

    rows = latest_snapshots(db)
    sku_counts: dict[str, int] = {}
    for row in rows:
        key = str(row["sku"]).casefold()
        sku_counts[key] = sku_counts.get(key, 0) + 1
    return {
        "suppliers": int(supplier_count),
        "pending_jobs": int(pending),
        "dead_jobs": int(dead),
        "versions": int(version_count),
        "imports": int(import_count),
        "catalog_products": len(sku_counts),
        "conflicts": sum(count > 1 for count in sku_counts.values()),
    }


def clear_runtime(db: str | Path) -> None:
    with connect(db) as conn:
        for table in (
            "audit",
            "changes",
            "snapshots",
            "versions",
            "issues",
            "imports",
            "jobs",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute(
            "DELETE FROM sqlite_sequence WHERE name IN "
            "('audit','changes','snapshots','versions','issues','imports','jobs')"
        )
