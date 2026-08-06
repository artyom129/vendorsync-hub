import json
from dataclasses import replace

from app import database as db
from app.main import demo_feed
from app.worker import Worker


def queue_demo(settings, kind):
    path, supplier = demo_feed(settings, kind)
    return db.enqueue(
        settings.database_path,
        "import_feed",
        supplier,
        json.dumps({"path": str(path)}),
        settings.max_job_attempts,
    )


def test_initial_import(settings):
    job_id = queue_demo(settings, "alpha")
    result = Worker(settings).run_once()
    assert result[0]["status"] == "completed"
    assert db.get_job(settings.database_path, job_id)["status"] == "completed"
    assert db.versions(settings.database_path)[0]["product_count"] == 3


def test_invalid_feed_goes_to_dead_letter(settings):
    job_id = queue_demo(settings, "invalid")
    result = Worker(settings).run_once()
    assert result[0]["status"] == "dead"
    job = db.get_job(settings.database_path, job_id)
    assert job["status"] == "dead"
    assert "validation issue" in job["last_error"].lower()
    assert db.imports(settings.database_path)[0]["status"] == "rejected"


def test_update_creates_changes(settings):
    worker = Worker(settings)
    for kind in ("alpha", "alpha_update"):
        queue_demo(settings, kind)
        worker.run_once()
    versions = [v for v in db.versions(settings.database_path) if v["supplier_id"] == "alpha"]
    assert len(versions) == 2
    assert versions[0]["new_items"] == 1
    assert versions[0]["changed_items"] == 2
    assert versions[0]["removed_items"] == 1


def test_empty_feed_is_rejected(settings):
    path = settings.incoming_dir / "alpha_empty.csv"
    path.write_text(
        "item_code,description,details,cost,available,group,currency_code\n",
        encoding="utf-8",
    )
    job_id = db.enqueue(
        settings.database_path,
        "import_feed",
        "alpha",
        json.dumps({"path": str(path)}),
        settings.max_job_attempts,
    )
    result = Worker(settings).run_once()
    assert result[0]["status"] == "dead"
    assert "no product rows" in db.get_job(settings.database_path, job_id)["last_error"]


def test_fractional_stock_is_rejected(settings):
    path = settings.incoming_dir / "alpha_fractional.csv"
    path.write_text(
        "item_code,description,details,cost,available,group,currency_code\n"
        "SKU-1,Item,Description,10,1.5,Test,USD\n",
        encoding="utf-8",
    )
    job_id = db.enqueue(
        settings.database_path,
        "import_feed",
        "alpha",
        json.dumps({"path": str(path)}),
        settings.max_job_attempts,
    )
    Worker(settings).run_once()
    assert "validation issue" in db.get_job(settings.database_path, job_id)["last_error"].lower()


def test_oversized_feed_is_rejected(settings):
    tiny = replace(settings, max_file_size_mb=1)
    path = tiny.incoming_dir / "alpha_large.csv"
    path.write_bytes(b"x" * (1024 * 1024 + 1))
    job_id = db.enqueue(
        tiny.database_path,
        "import_feed",
        "alpha",
        json.dumps({"path": str(path)}),
        tiny.max_job_attempts,
    )
    Worker(tiny).run_once()
    assert "exceeds" in db.get_job(tiny.database_path, job_id)["last_error"].lower()


def test_malformed_json_is_rejected_without_retries(settings):
    path = settings.incoming_dir / "northstar_broken.json"
    path.write_text("{not-json", encoding="utf-8")
    job_id = db.enqueue(
        settings.database_path,
        "import_feed",
        "northstar",
        json.dumps({"path": str(path)}),
        settings.max_job_attempts,
    )
    result = Worker(settings).run_once()
    assert result[0]["status"] == "dead"
    job = db.get_job(settings.database_path, job_id)
    assert "parsing failed" in job["last_error"].lower()


def test_manual_retry_resets_attempts_and_keeps_rejected_path(settings):
    job_id = queue_demo(settings, "invalid")
    worker = Worker(settings)
    worker.run_once()
    job = db.get_job(settings.database_path, job_id)
    rejected_path = json.loads(job["payload_json"])["path"]
    assert "rejected" in rejected_path
    assert db.retry(settings.database_path, job_id) is True
    reset = db.get_job(settings.database_path, job_id)
    assert reset["attempts"] == 0
    result = worker.run_once()
    assert result[0]["status"] == "dead"
    assert "no longer exists" not in result[0]["error"].lower()
