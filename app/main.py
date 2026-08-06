from __future__ import annotations

import asyncio
import csv
import json
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import database as db
from .catalog import unified
from .config import Settings, load_settings
from .reporting import to_csv, to_xlsx
from .worker import Worker


BASE = Path(__file__).parent
ALLOWED_EXTENSIONS = {".csv", ".json"}
DEMO_KINDS = {"alpha", "alpha_update", "northstar", "invalid"}


def register(settings: Settings) -> None:
    for supplier in settings.suppliers:
        db.upsert_supplier(
            settings.database_path,
            {
                "id": supplier.id,
                "name": supplier.name,
                "priority": supplier.priority,
                "format": supplier.format,
                "filename_pattern": supplier.filename_pattern,
                "currency": supplier.currency,
                "mapping_json": json.dumps(
                    supplier.mapping,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
            },
        )


def demo_feed(settings: Settings, kind: str) -> tuple[Path, str]:
    if kind not in DEMO_KINDS:
        raise ValueError("Unknown demo kind")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    if kind in {"alpha", "alpha_update", "invalid"}:
        path = settings.incoming_dir / f"alpha_{kind}_{stamp}.csv"
        if kind == "alpha":
            rows = [
                ["item_code", "description", "details", "cost", "available", "group", "currency_code"],
                ["SKU-100", "Wireless Mouse", "Ergonomic 2.4 GHz mouse", "18.50", "45", "Accessories", "USD"],
                ["SKU-200", "Mechanical Keyboard", "Blue switches", "62", "20", "Accessories", "USD"],
                ["SKU-300", "USB-C Dock", "Nine-port dock", "89", "12", "Connectivity", "USD"],
            ]
        elif kind == "alpha_update":
            rows = [
                ["item_code", "description", "details", "cost", "available", "group", "currency_code"],
                ["SKU-100", "Wireless Mouse", "Ergonomic 2.4 GHz mouse", "17.25", "38", "Accessories", "USD"],
                ["SKU-200", "Mechanical Keyboard Pro", "Metal frame and blue switches", "68", "14", "Accessories", "USD"],
                ["SKU-400", "Laptop Stand", "Aluminium stand", "35", "20", "Office", "USD"],
            ]
        else:
            rows = [
                ["item_code", "description", "details", "cost", "available", "group", "currency_code"],
                ["", "Missing SKU", "", "10", "5", "Broken", "USD"],
                ["SKU-X", "", "", "-5", "-2", "Broken", "US"],
                ["SKU-X", "Duplicate", "", "20", "3", "Broken", "USD"],
            ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            csv.writer(handle).writerows(rows)
        return path, "alpha"

    path = settings.incoming_dir / f"northstar_{stamp}.json"
    payload = {
        "products": [
            {
                "sku": "SKU-100",
                "title": "Wireless Mouse",
                "summary": "Silent office mouse",
                "pricing": {"amount": "15.90", "currency": "USD"},
                "inventory": {"qty": 80},
                "department": "Accessories",
            },
            {
                "sku": "SKU-300",
                "title": "USB-C Dock Station",
                "summary": "Eight-port dock",
                "pricing": {"amount": "104", "currency": "USD"},
                "inventory": {"qty": 30},
                "department": "Connectivity",
            },
            {
                "sku": "SKU-500",
                "title": "Web Camera",
                "summary": "1080p camera",
                "pricing": {"amount": "44", "currency": "USD"},
                "inventory": {"qty": 22},
                "department": "Video",
            },
        ]
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path, "northstar"


def _unique_upload_path(settings: Settings, filename: str) -> Path:
    safe_name = Path(filename or "feed.bin").name
    source = Path(safe_name)
    return settings.incoming_dir / (
        f"{source.stem}_{uuid4().hex[:8]}{source.suffix.lower()}"
    )


async def _save_upload(settings: Settings, file: UploadFile) -> Path:
    target = _unique_upload_path(settings, file.filename or "feed.bin")
    if target.suffix.lower() not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=415,
            detail="Only CSV and JSON files are supported",
        )

    max_bytes = settings.max_file_size_mb * 1024 * 1024
    total = 0
    try:
        with target.open("wb") as handle:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds the {settings.max_file_size_mb} MB limit",
                    )
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await file.close()
    return target


def _supplier_exists(settings: Settings, supplier_id: str | None) -> bool:
    return supplier_id is None or any(s.id == supplier_id for s in settings.suppliers)


def create_app(settings_override: Settings | None = None) -> FastAPI:
    settings = settings_override or load_settings()
    db.init_db(settings.database_path)
    register(settings)
    worker = Worker(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stop = asyncio.Event()

        async def worker_loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.to_thread(worker.run_once)
                except Exception as exc:
                    db.audit(
                        settings.database_path,
                        "worker_loop_error",
                        "error",
                        f"Background worker loop: {exc}",
                    )
                try:
                    await asyncio.wait_for(
                        stop.wait(), timeout=settings.scan_interval_seconds
                    )
                except asyncio.TimeoutError:
                    continue

        task = asyncio.create_task(worker_loop())
        yield
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    app = FastAPI(
        title=settings.app_name,
        description="Supplier catalog normalization and synchronization control center.",
        version="1.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.worker = worker

    templates = Jinja2Templates(directory=BASE / "templates")
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    def catalog_data():
        return unified(
            db.latest_snapshots(settings.database_path),
            settings.conflict_price_threshold_percent,
        )

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request):
        catalog, conflicts = catalog_data()
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "app_name": settings.app_name,
                "stats": db.stats(settings.database_path),
                "suppliers": db.suppliers(settings.database_path),
                "catalog": catalog[:10],
                "jobs": db.jobs(settings.database_path, limit=8),
                "versions": db.versions(settings.database_path, limit=8),
                "events": db.audit_events(settings.database_path, 10),
                "conflicts": conflicts[:5],
            },
        )

    @app.get("/catalog", response_class=HTMLResponse)
    async def catalog_page(request: Request, q: str = "", conflict: str = ""):
        catalog, _ = catalog_data()
        if q:
            needle = q.casefold()
            catalog = [
                item
                for item in catalog
                if needle in str(item["sku"]).casefold()
                or needle in str(item["name"]).casefold()
                or needle in str(item["category"]).casefold()
            ]
        if conflict == "yes":
            catalog = [item for item in catalog if item["has_conflict"]]
        return templates.TemplateResponse(
            request=request,
            name="catalog.html",
            context={
                "app_name": settings.app_name,
                "catalog": catalog,
                "q": q,
                "conflict": conflict,
            },
        )

    @app.get("/products/{sku}", response_class=HTMLResponse)
    async def product(request: Request, sku: str):
        offers = [
            item
            for item in db.latest_snapshots(settings.database_path)
            if str(item["sku"]).casefold() == sku.casefold()
        ]
        if not offers:
            raise HTTPException(status_code=404, detail="Product not found")
        return templates.TemplateResponse(
            request=request,
            name="product.html",
            context={"app_name": settings.app_name, "sku": sku, "offers": offers},
        )

    @app.get("/jobs", response_class=HTMLResponse)
    async def jobs_page(request: Request, status: str = ""):
        return templates.TemplateResponse(
            request=request,
            name="jobs.html",
            context={
                "app_name": settings.app_name,
                "jobs": db.jobs(settings.database_path, status or None, 300),
                "status": status,
            },
        )

    @app.get("/versions", response_class=HTMLResponse)
    async def versions_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="versions.html",
            context={
                "app_name": settings.app_name,
                "versions": db.versions(settings.database_path),
                "changes": db.changes(settings.database_path),
            },
        )

    @app.post("/demo/{kind}")
    async def demo(kind: str):
        if kind not in DEMO_KINDS:
            raise HTTPException(status_code=404, detail="Unknown demo feed")
        path, supplier = demo_feed(settings, kind)
        db.enqueue(
            settings.database_path,
            "import_feed",
            supplier,
            json.dumps({"path": str(path)}),
            settings.max_job_attempts,
        )
        await asyncio.to_thread(worker.run_once)
        return RedirectResponse("/", status_code=303)

    @app.post("/upload")
    async def upload(file: UploadFile = File(...), supplier_id: str | None = None):
        if not _supplier_exists(settings, supplier_id):
            raise HTTPException(status_code=422, detail="Unknown supplier")
        target = await _save_upload(settings, file)
        db.enqueue(
            settings.database_path,
            "import_feed",
            supplier_id or None,
            json.dumps({"path": str(target)}),
            settings.max_job_attempts,
        )
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/jobs/run")
    async def run_jobs():
        await asyncio.to_thread(worker.run_once)
        return RedirectResponse("/jobs", status_code=303)

    @app.post("/jobs/{job_id}/retry")
    async def retry_job(job_id: int):
        if not db.get_job(settings.database_path, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        if not db.retry(settings.database_path, job_id):
            raise HTTPException(status_code=409, detail="Job cannot be retried")
        await asyncio.to_thread(worker.run_once)
        return RedirectResponse("/jobs", status_code=303)

    @app.get("/api/stats")
    async def api_stats():
        return db.stats(settings.database_path)

    @app.get("/api/suppliers")
    async def api_suppliers():
        return {"items": db.suppliers(settings.database_path)}

    @app.post("/api/demo/{kind}")
    async def api_demo(kind: str):
        if kind not in DEMO_KINDS:
            raise HTTPException(status_code=404, detail="Unknown demo feed")
        path, supplier = demo_feed(settings, kind)
        job_id = db.enqueue(
            settings.database_path,
            "import_feed",
            supplier,
            json.dumps({"path": str(path)}),
            settings.max_job_attempts,
        )
        return {
            "job_id": job_id,
            "results": await asyncio.to_thread(worker.run_once),
        }

    @app.post("/api/feeds/upload")
    async def api_upload(
        file: UploadFile = File(...),
        supplier_id: str | None = None,
        run_now: bool = True,
    ):
        if not _supplier_exists(settings, supplier_id):
            raise HTTPException(status_code=422, detail="Unknown supplier")
        target = await _save_upload(settings, file)
        job_id = db.enqueue(
            settings.database_path,
            "import_feed",
            supplier_id or None,
            json.dumps({"path": str(target)}),
            settings.max_job_attempts,
        )
        results = await asyncio.to_thread(worker.run_once) if run_now else []
        return {"job_id": job_id, "results": results}

    @app.get("/api/jobs")
    async def api_jobs(status: str | None = None):
        return {"items": db.jobs(settings.database_path, status)}

    @app.post("/api/jobs/run")
    async def api_run():
        return {"results": await asyncio.to_thread(worker.run_once)}

    @app.post("/api/jobs/{job_id}/retry")
    async def api_retry(job_id: int):
        if not db.get_job(settings.database_path, job_id):
            raise HTTPException(status_code=404, detail="Job not found")
        if not db.retry(settings.database_path, job_id):
            raise HTTPException(status_code=409, detail="Job cannot be retried")
        return {"changed": True}

    @app.get("/api/catalog")
    async def api_catalog():
        catalog, conflicts = catalog_data()
        return {"items": catalog, "conflict_count": len(conflicts)}

    @app.get("/api/products/{sku}")
    async def api_product(sku: str):
        offers = [
            item
            for item in db.latest_snapshots(settings.database_path)
            if str(item["sku"]).casefold() == sku.casefold()
        ]
        if not offers:
            raise HTTPException(status_code=404, detail="Product not found")
        return {"sku": sku, "offers": offers}

    @app.get("/api/versions")
    async def api_versions():
        return {"items": db.versions(settings.database_path)}

    @app.get("/api/conflicts")
    async def api_conflicts():
        return {"items": catalog_data()[1]}

    @app.get("/exports/catalog.csv")
    async def csv_export():
        return Response(
            to_csv(catalog_data()[0]),
            media_type="text/csv",
            headers={
                "Content-Disposition": "attachment; filename=vendorsync_catalog.csv"
            },
        )

    @app.get("/exports/catalog.xlsx")
    async def xlsx_export():
        catalog, conflicts = catalog_data()
        content = to_xlsx(
            catalog,
            conflicts,
            db.versions(settings.database_path),
            db.changes(settings.database_path),
            db.imports(settings.database_path),
        )
        return Response(
            content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": "attachment; filename=vendorsync_report.xlsx"
            },
        )

    @app.get("/health")
    async def health():
        current_stats = db.stats(settings.database_path)
        return {
            "status": "ok",
            "suppliers": len(settings.suppliers),
            "pending_jobs": current_stats["pending_jobs"],
            "database": str(settings.database_path),
        }

    return app


app = create_app()
