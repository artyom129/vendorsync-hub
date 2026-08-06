import time

from fastapi.testclient import TestClient

from app import database as db
from app.main import create_app


def test_demo_and_exports(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.post("/api/demo/alpha").status_code == 200
        assert client.post("/api/demo/northstar").status_code == 200
        body = client.get("/api/catalog").json()
        assert len(body["items"]) == 4
        assert body["conflict_count"] == 2
        assert len(client.get("/api/products/SKU-100").json()["offers"]) == 2
        xlsx = client.get("/exports/catalog.xlsx")
        assert xlsx.status_code == 200
        assert xlsx.content.startswith(b"PK")


def test_unknown_demo_returns_404(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.post("/api/demo/not-real").status_code == 404


def test_upload_rejects_unsupported_extension_and_supplier(settings):
    app = create_app(settings)
    with TestClient(app) as client:
        unsupported = client.post(
            "/api/feeds/upload",
            files={"file": ("feed.txt", b"hello", "text/plain")},
        )
        assert unsupported.status_code == 415
        unknown = client.post(
            "/api/feeds/upload?supplier_id=missing",
            files={"file": ("alpha.csv", b"x", "text/csv")},
        )
        assert unknown.status_code == 422


def test_upload_same_filename_does_not_overwrite_pending_file(settings):
    app = create_app(settings)
    first_content = (
        b"item_code,description,details,cost,available,group,currency_code\n"
        b"SKU-900,Item,Description,10,1,Test,USD\n"
    )
    second_content = first_content.replace(b"SKU-900", b"SKU-901")
    with TestClient(app) as client:
        first = client.post(
            "/api/feeds/upload?run_now=false&supplier_id=alpha",
            files={"file": ("alpha_same.csv", first_content, "text/csv")},
        )
        second = client.post(
            "/api/feeds/upload?run_now=false&supplier_id=alpha",
            files={"file": ("alpha_same.csv", second_content, "text/csv")},
        )
        assert first.status_code == 200
        assert second.status_code == 200
        jobs = db.jobs(settings.database_path)
        paths = {job["payload_json"] for job in jobs}
        assert len(paths) == 2


def test_background_worker_processes_queued_job(settings):
    app = create_app(settings)
    content = (
        b"item_code,description,details,cost,available,group,currency_code\n"
        b"SKU-777,Background Item,Description,10,1,Test,USD\n"
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/feeds/upload?run_now=false&supplier_id=alpha",
            files={"file": ("alpha_background.csv", content, "text/csv")},
        )
        job_id = response.json()["job_id"]
        deadline = time.time() + 3
        status = None
        while time.time() < deadline:
            status = db.get_job(settings.database_path, job_id)["status"]
            if status == "completed":
                break
            time.sleep(0.1)
        assert status == "completed"


def test_upload_size_limit_is_enforced(settings):
    from dataclasses import replace

    tiny = replace(settings, max_file_size_mb=1)
    app = create_app(tiny)
    with TestClient(app) as client:
        response = client.post(
            "/api/feeds/upload?supplier_id=alpha",
            files={"file": ("alpha_large.csv", b"x" * (1024 * 1024 + 1), "text/csv")},
        )
        assert response.status_code == 413
        assert not list(tiny.incoming_dir.glob("alpha_large*.csv"))
