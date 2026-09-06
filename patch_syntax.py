import os
content = open("beatcanvas/worker.py", "r", encoding="utf-8").read()
if 'print(f"Job {job.id} failed:\n{err}")' in content:
    content = content.replace('print(f"Job {job.id} failed:\n{err}")', 'print(f"Job {job.id} failed:\\n{err}")')
    open("beatcanvas/worker.py", "w", encoding="utf-8").write(content)
    print("Fixed!")
else:
    print("Not found")
