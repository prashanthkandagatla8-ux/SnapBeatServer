import re

with open("C:/MyProjects/SnapBeatServer/beatcanvas/app.py", "r") as f:
    code = f.read()

replacement = """@app.get("/api/render/status/{job_id}")
def render_status(job_id: int):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    
    display_stage = job.stage
    if job.status == "queued":
        display_stage = "Waiting in queue..."
        
    return {
        "status": job.status,
        "stage": display_stage,
        "progress": round((job.progress or 0.0) * 100),
        "error": job.error
    }"""

code = re.sub(r'@app\.get\("/api/render/status/\{job_id\}"\).*?return \{.*?\}', replacement, code, flags=re.DOTALL)

with open("C:/MyProjects/SnapBeatServer/beatcanvas/app.py", "w") as f:
    f.write(code)
