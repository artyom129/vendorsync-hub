<div align="center">

# VendorSync Hub

### Supplier catalog ingestion, normalization, versioning, conflict detection, and background synchronization

**Python · FastAPI · SQLite · CSV/JSON · Background Jobs · Excel Reports**

</div>

VendorSync Hub is a production-style automation platform for businesses that receive product catalogs from multiple suppliers in different formats.

It ingests CSV and JSON feeds, maps supplier-specific fields into one canonical product schema, validates rows, rejects duplicate files, creates immutable catalog versions, detects product changes and cross-supplier conflicts, and exports a unified catalog.

## Why this project exists

Supplier feeds rarely agree on field names or structure:

```text
Alpha CSV                    Northstar JSON
item_code                    sku
description                  title
cost                         pricing.amount
available                    inventory.qty
```

VendorSync Hub converts both into:

```text
sku, name, description, price, stock, currency, category, supplier
```

## Main capabilities

- Multiple supplier configurations in `config.toml`
- CSV and nested JSON adapters
- Supplier-specific field mappings
- Canonical product normalization
- Required-field and type validation
- Duplicate SKU detection inside a feed
- SHA-256 duplicate-file protection
- Persistent SQLite background job queue with an automatic worker loop
- Retryable system failures and dead-letter handling for permanent feed errors
- Immutable supplier catalog versions
- New, changed, unchanged, and removed item counts
- Field-level change history
- Unified multi-supplier catalog
- Supplier-priority winner selection
- Conflict and price-difference detection
- Complete import and audit history
- Streaming upload limits and unique file staging
- Spreadsheet-formula protection in CSV and Excel exports
- Web dashboard and REST API
- CSV and formatted Excel exports
- Docker, GitHub Actions, and automated tests

## Quick start

```bat
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python seed_demo.py
python run.py
```

Open:

```text
http://127.0.0.1:8000
```

Swagger:

```text
http://127.0.0.1:8000/docs
```

## Test it from the website

The dashboard contains demo actions:

- **Import Alpha feed** — initial CSV supplier catalog
- **Import Northstar feed** — nested JSON catalog with overlapping SKUs
- **Import Alpha update** — price, stock, name, new SKU, and removed SKU changes
- **Import invalid feed** — validation failure and retry state

## Architecture

```text
Web upload / REST API / demo feed
               ↓
       Persistent job queue
               ↓
         Background worker
               ↓
       Supplier feed adapter
               ↓
      Mapping + normalization
               ↓
 Validation + duplicate protection
               ↓
      Version comparison engine
               ↓
     Immutable product snapshots
               ↓
 Unified catalog + conflict engine
               ↓
 Dashboard / API / CSV / Excel
```

## REST API

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/api/stats` | Dashboard statistics |
| `GET` | `/api/suppliers` | Supplier configurations |
| `POST` | `/api/demo/{kind}` | Create and process demo feed |
| `POST` | `/api/feeds/upload` | Upload CSV or JSON |
| `GET` | `/api/jobs` | Job queue |
| `POST` | `/api/jobs/run` | Run ready jobs |
| `POST` | `/api/jobs/{id}/retry` | Retry failed job |
| `GET` | `/api/catalog` | Unified catalog |
| `GET` | `/api/products/{sku}` | Current supplier offers |
| `GET` | `/api/versions` | Version history |
| `GET` | `/api/conflicts` | Supplier conflicts |
| `GET` | `/exports/catalog.csv` | Catalog CSV |
| `GET` | `/exports/catalog.xlsx` | Full Excel report |

## Project structure

```text
vendorsync-hub/
├── app/
│   ├── adapters.py
│   ├── catalog.py
│   ├── config.py
│   ├── database.py
│   ├── importer.py
│   ├── main.py
│   ├── reporting.py
│   ├── worker.py
│   ├── static/
│   └── templates/
├── data/
│   ├── incoming/
│   ├── archive/
│   └── rejected/
├── docs/
├── tests/
├── config.toml
├── seed_demo.py
└── run.py
```

## Portfolio context

This is a personal software engineering project. It contains no client data and does not claim to be client work.

## License

MIT
