import json
from app.config import load_settings
from app.main import demo_feed,register
from app.worker import Worker
from app import database as db

settings=load_settings();db.init_db(settings.database_path);register(settings);db.clear_runtime(settings.database_path)
for folder in (settings.incoming_dir,settings.archive_dir,settings.rejected_dir):
    for path in folder.iterdir():
        if path.is_file() and path.name!=".gitkeep":path.unlink()
worker=Worker(settings)
for kind in ("alpha","northstar","alpha_update","invalid"):
    path,supplier=demo_feed(settings,kind)
    job=db.enqueue(settings.database_path,"import_feed",supplier,json.dumps({"path":str(path)}),settings.max_job_attempts)
    print(kind,job,worker.run_once())
print("Demo history created. Run: python run.py")
