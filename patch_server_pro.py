import re
from pathlib import Path

app_path = Path(r"C:\MyProjects\SnapBeatServer\beatcanvas\app.py")
content = app_path.read_text(encoding="utf-8")

old_sig = """def render_mobile(
    audio: UploadFile = File(...),
    photos: List[UploadFile] = File(...)
):"""

new_sig = """def render_mobile(
    audio: UploadFile = File(...),
    photos: List[UploadFile] = File(...),
    template: str = Form(None)
):"""

old_job_create = """    job = job_store.create(f"mobile_{job_id_str[:8]}", AUTO_TEMPLATE, options)"""
new_job_create = """    chosen_template = f"templates/{template}.json" if template else AUTO_TEMPLATE
    job = job_store.create(f"mobile_{job_id_str[:8]}", chosen_template, options)"""

if old_sig in content:
    content = content.replace(old_sig, new_sig)
    content = content.replace(old_job_create, new_job_create)
    app_path.write_text(content, encoding="utf-8")
    print("Patched app.py!")
else:
    print("Signature not found in app.py!")
