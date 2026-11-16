import json
import os
import time
from dataclasses import replace

from fastapi.testclient import TestClient

from app import database as db
from app.importer import parse_amount
from app.main import create_app
from app.worker import Worker


def feed(settings, name, body):
    path = settings.incoming_dir / name
    path.write_text(body, encoding="utf-8")
    return path


HEADER = "item_code,description,details,cost,available,group,currency_code\n"


def test_prices_in_regional_formats():
    assert str(parse_amount("18.50")) == "18.50"
    assert str(parse_amount("1,234.50")) == "1234.50"
    assert str(parse_amount("1.234,50")) == "1234.50"
    assert str(parse_amount("1 234,50")) == "1234.50"
    assert str(parse_amount("12,5")) == "12.5"
    assert str(parse_amount("1,234,567")) == "1234567"


def test_regional_price_is_accepted_in_a_feed(settings):
    path = feed(settings, "alpha_eu.csv", HEADER + 'A-1,Cable,,"1.234,50",4,Misc,EUR\n')
    db.enqueue(settings.database_path, "import_feed", "alpha",
               json.dumps({"path": str(path)}), 3)
    assert Worker(settings).run_once()[0]["status"] == "completed"
    assert db.latest_snapshots(settings.database_path)[0]["price"] == "1234.50"


def test_job_left_running_by_a_crash_is_picked_up_again(settings):
    path = feed(settings, "alpha_crash.csv", HEADER + "A-1,Cable,,5,4,Misc,USD\n")
    job_id = db.enqueue(settings.database_path, "import_feed", "alpha",
                        json.dumps({"path": str(path)}), 3)
    assert db.claim(settings.database_path)[0]["id"] == job_id
    # the process died here: the job stays 'running'
    assert Worker(settings).run_once() == []
    stale = replace(settings, job_timeout_seconds=1.0)
    time.sleep(1.2)
    result = Worker(stale).run_once()
    assert result[0]["status"] == "completed"
    assert db.get_job(settings.database_path, job_id)["status"] == "completed"


def test_stale_job_without_attempts_left_goes_to_dead_letter(settings):
    path = feed(settings, "alpha_last.csv", HEADER + "A-1,Cable,,5,4,Misc,USD\n")
    job_id = db.enqueue(settings.database_path, "import_feed", "alpha",
                        json.dumps({"path": str(path)}), 1)
    db.claim(settings.database_path)
    time.sleep(1.2)
    assert db.recover_stale(settings.database_path, 1.0) == 1
    job = db.get_job(settings.database_path, job_id)
    assert job["status"] == "dead"
    assert "stopped" in job["last_error"]


def test_file_dropped_into_incoming_is_queued_and_imported(settings):
    path = feed(settings, "alpha_drop.csv", HEADER + "A-1,Cable,,5,4,Misc,USD\n")
    quick = replace(settings, settle_seconds=0.0)
    worker = Worker(quick)
    assert worker.scan_incoming() == 1
    assert worker.scan_incoming() == 0  # already queued
    assert worker.run_once()[0]["status"] == "completed"
    assert not path.exists()
    assert worker.scan_incoming() == 0


def test_file_still_being_copied_is_left_alone(settings):
    feed(settings, "alpha_slow.csv", HEADER + "A-1,Cable,,5,4,Misc,USD\n")
    patient = replace(settings, settle_seconds=60.0)
    assert Worker(patient).scan_incoming() == 0


def test_other_files_in_incoming_are_ignored(settings):
    feed(settings, "notes.txt", "hello")
    feed(settings, "alpha_ok.csv", HEADER + "A-1,Cable,,5,4,Misc,USD\n")
    quick = replace(settings, settle_seconds=0.0)
    assert Worker(quick).scan_incoming() == 1


def test_failed_file_is_not_requeued_forever(settings):
    path = feed(settings, "alpha_gone.csv", HEADER + "A-1,Cable,,5,4,Misc,USD\n")
    quick = replace(settings, settle_seconds=0.0)
    worker = Worker(quick)
    worker.scan_incoming()
    os.utime(path, (0, 0))
    db.claim(settings.database_path)
    db.fail_job(settings.database_path, 1, "boom", 3, 3, retryable=True)
    assert worker.scan_incoming() == 0


def test_health_does_not_expose_server_paths(settings):
    with TestClient(create_app(settings)) as client:
        body = client.get("/health").json()
    assert "database" not in body
    assert body["status"] == "ok"


def test_sku_with_slash_and_hash_opens(settings):
    path = feed(settings, "alpha_odd.csv", HEADER + "AB/12#X,Odd cable,,5,4,Misc,USD\n")
    db.enqueue(settings.database_path, "import_feed", "alpha",
               json.dumps({"path": str(path)}), 3)
    with TestClient(create_app(settings)) as client:
        client.post("/api/jobs/run")
        assert client.get("/api/products/AB/12%23X").status_code == 200
        page = client.get("/catalog")
        assert "/products/AB/12%23X" in page.text
        assert client.get("/products/AB/12%23X").status_code == 200
