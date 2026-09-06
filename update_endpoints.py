import re

with open("C:/MyProjects/SnapBeatServer/beatcanvas/app.py", "r") as f:
    code = f.read()

target = """    job = job_store.create(f"mobile_{job_id_str[:8]}", AUTO_TEMPLATE, options)
    worker.notify()

    # Poll for completion (Wait up to 5 minutes)
    for _ in range(300):
        await asyncio.sleep(1)
        current_job = job_store.get(job.id)
        if current_job.status == store.STATUS_DONE:
            return FileResponse(current_job.output, media_type="video/mp4", filename=f"snapbeat_{job_id_str[:8]}.mp4")
        elif current_job.status in (store.STATUS_FAILED, store.STATUS_CANCELLED):
            raise HTTPException(status_code=500, detail=f"Render failed: {current_job.error}")

    raise HTTPException(status_code=504, detail="Render timed out")"""

replacement = """    job = job_store.create(f"mobile_{job_id_str[:8]}", AUTO_TEMPLATE, options)
    worker.notify()
    return {"job_id": job.id}

@app.get("/api/render/status/{job_id}")
def render_status(job_id: int):
    job = job_store.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="job not found")
    return {
        "status": job.status,
        "stage": job.stage,
        "progress": round((job.progress or 0.0) * 100),
        "error": job.error
    }

@app.get("/api/render/download/{job_id}")
def render_download(job_id: int):
    job = job_store.get(job_id)
    if not job or job.status != store.STATUS_DONE:
        raise HTTPException(status_code=400, detail="job not done")
    return FileResponse(job.output, media_type="video/mp4", filename=f"snapbeat_{job_id}.mp4")"""

code = code.replace(target, replacement)

with open("C:/MyProjects/SnapBeatServer/beatcanvas/app.py", "w") as f:
    f.write(code)
