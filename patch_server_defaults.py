import re
from pathlib import Path

app_path = Path(r"C:\MyProjects\SnapBeatServer\beatcanvas\app.py")
content = app_path.read_text(encoding="utf-8")

old_template_logic = """    chosen_template = f"templates/{template}.json" if template else AUTO_TEMPLATE"""
new_template_logic = """    chosen_template = f"templates/{template}.json" if template else "templates/simple.json\""""

if old_template_logic in content:
    content = content.replace(old_template_logic, new_template_logic)
    app_path.write_text(content, encoding="utf-8")
    print("Patched app.py AUTO_TEMPLATE logic")
else:
    print("Could not find old logic in app.py")

worker_path = Path(r"C:\MyProjects\SnapBeatServer\beatcanvas\worker.py")
content = worker_path.read_text(encoding="utf-8")
if 'AUTO_TEMPLATE = "templates/beat-cut.json"' in content:
    content = content.replace('AUTO_TEMPLATE = "templates/beat-cut.json"', 'AUTO_TEMPLATE = "templates/simple.json"')
    worker_path.write_text(content, encoding="utf-8")
    print("Patched worker.py AUTO_TEMPLATE")

