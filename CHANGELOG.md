# Changelog

## 1.1.0 — 2026-08-06

### Reliability

- Added an in-process background worker loop during FastAPI lifespan.
- Made job claiming transactional so two workers cannot claim the same queued job.
- Separated permanent feed errors from retryable system failures.
- Reset attempts and timestamps correctly when a job is manually retried.
- Preserved the rejected file path in the job payload so retries do not point to a missing source file.
- Made version, snapshot, and change creation a single database transaction.
- Enabled SQLite WAL mode and foreign-key enforcement.

### Validation and safety

- Rejected empty, malformed, oversized, and unsupported feed files cleanly.
- Prevented fractional stock values from being silently truncated.
- Canonicalized SKUs and made comparison case-insensitive.
- Strengthened three-letter currency validation.
- Streamed uploads to disk with a hard size limit instead of reading the entire file into memory.
- Generated unique upload paths to prevent concurrent files from overwriting each other.
- Neutralized spreadsheet formulas in CSV and Excel exports.

### Catalog and interface

- Fixed zero-price conflict calculations.
- Added cross-currency conflict detection.
- Fixed Docker networking by binding the application to `0.0.0.0` by default.
- Hardened the upload layout, file input, responsive grid, and table overflow behavior.
- Added clear API errors for unknown demos, suppliers, file types, and invalid retries.

### Verification

- Expanded the automated suite from 6 to 20 tests.
- Added API, background worker, malformed feed, upload collision, size limit, retry, conflict, and export safety coverage.

## 1.0.0 — 2026-08-06

- Initial supplier feed ingestion, normalization, catalog versioning, conflict detection, API, dashboard, reports, Docker packaging, and tests.
