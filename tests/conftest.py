from dataclasses import replace
from pathlib import Path
import pytest
from app.config import load_settings
from app.main import register
from app import database as db

@pytest.fixture
def settings(tmp_path:Path):
    base=load_settings(Path(__file__).resolve().parent.parent/"config.toml")
    s=replace(base,database_path=tmp_path/"test.db",incoming_dir=tmp_path/"incoming",archive_dir=tmp_path/"archive",rejected_dir=tmp_path/"rejected")
    for p in (s.incoming_dir,s.archive_dir,s.rejected_dir):p.mkdir(parents=True,exist_ok=True)
    db.init_db(s.database_path);register(s);return s
