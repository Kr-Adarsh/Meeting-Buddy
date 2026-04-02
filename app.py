"""
Meeting Buddy — FastAPI Backend
Minimal API for deploying, monitoring, and stopping the Meet bot.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
import uuid
import json
import os

from meet_bot import MeetBot
from summarizer import generate_meeting_summary

# ── App ──────────────────────────────────────

app = FastAPI(title="Meeting Buddy API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory stores ────────────────────────

jobs_db: Dict[str, dict] = {}
bots_db: Dict[str, MeetBot] = {}


# ── Models ──────────────────────────────────

class MeetRequest(BaseModel):
    url: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    message: str


# ── Background worker ───────────────────────

async def _run_bot_task(job_id: str, meet_url: str):
    """Runs the bot in a background thread."""
    bot = bots_db.get(job_id)
    if not bot:
        return

    try:
        jobs_db[job_id]["status"] = "joining"

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, bot.run)

        jobs_db[job_id]["status"] = result.get("status", "completed")
        jobs_db[job_id]["result"] = result
        if result.get("error"):
            jobs_db[job_id]["error"] = result["error"]

    except Exception as e:
        jobs_db[job_id]["status"] = "failed"
        jobs_db[job_id]["error"] = str(e)
    finally:
        bots_db.pop(job_id, None)


# ── Endpoints ───────────────────────────────

@app.post("/deploy-bot", response_model=JobResponse)
async def deploy_bot(request: MeetRequest, background_tasks: BackgroundTasks):
    """Start the bot for a given Google Meet URL."""
    if "meet.google.com" not in request.url:
        raise HTTPException(status_code=400, detail="Invalid Google Meet URL")

    meet_id = request.url.split('meet.google.com/')[-1].split('?')[0].strip('/')
    if not meet_id:
        meet_id = str(uuid.uuid4())[:8]
        
    dt_str = datetime.now().strftime("%d-%m-%y-%H-%M-%S")
    job_id = f"Meet-{meet_id}-{dt_str}"

    bot = MeetBot(meet_url=request.url, job_id=job_id)
    bots_db[job_id] = bot

    jobs_db[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "url": request.url,
        "result": None,
        "error": None,
    }

    background_tasks.add_task(_run_bot_task, job_id, request.url)

    return JobResponse(
        job_id=job_id, status="pending", message="Meeting Buddy is deploying!"
    )


@app.get("/job-status/{job_id}")
async def get_job_status(job_id: str):
    """Get current job status. Includes live captions if bot is active."""
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")

    job = dict(jobs_db[job_id])

    # Attach live data from active bot
    bot = bots_db.get(job_id)
    if bot:
        job["status"] = bot.status
        job["captions_count"] = len(getattr(bot, 'captions', []))
        job["live_captions"] = getattr(bot, 'captions', [])[-20:]  # last 20 for live display

    return job


@app.get("/jobs")
async def get_all_jobs():
    """Get all historical and active jobs."""
    all_jobs = []
    
    # 1. Fetch from running DB
    for jid, job in jobs_db.items():
        job_copy = dict(job)
        bot = bots_db.get(jid)
        if bot:
            job_copy["status"] = bot.status
            job_copy["captions_count"] = len(bot.captions) if hasattr(bot, 'captions') else 0
        else:
            # Bot concluded but FastAPI hasn't refreshed memory. Reconstruct keys from result.
            res = job.get("result", {})
            if res:
                job_copy["duration_seconds"] = res.get("duration_seconds", 0)
                job_copy["has_audio"] = bool(res.get("audio_file"))
                job_copy["has_captions"] = bool(res.get("captions_file"))
                job_copy["captions_count"] = res.get("captions_count", 0)
                job_copy["has_summary"] = os.path.exists(os.path.join("recordings", jid, "summary.md"))
            else:
                # If bot failed catastrophically early, probe native folder as ultimate fallback
                rec_dir = os.path.join("recordings", jid)
                job_copy["duration_seconds"] = 0
                job_copy["has_audio"] = os.path.exists(os.path.join(rec_dir, "audio.wav"))
                job_copy["has_captions"] = os.path.exists(os.path.join(rec_dir, "captions.txt"))
                job_copy["has_summary"] = os.path.exists(os.path.join(rec_dir, "summary.md"))
                
        all_jobs.append(job_copy)
        
    running_ids = set(jobs_db.keys())
    
    # 2. Fetch from disk
    rec_dir = "recordings"
    if os.path.exists(rec_dir):
        for folder in os.listdir(rec_dir):
            if folder in running_ids:
                continue
            meta_path = os.path.join(rec_dir, folder, "metadata.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                        # Unconditionally verify physical presence to allow deletion sync
                        meta["has_audio"] = os.path.exists(os.path.join(rec_dir, folder, "audio.wav"))
                        meta["has_captions"] = os.path.exists(os.path.join(rec_dir, folder, "captions.txt"))
                        meta["has_summary"] = os.path.exists(os.path.join(rec_dir, folder, "summary.md"))
                        meta["summary_failed"] = os.path.exists(os.path.join(rec_dir, folder, ".summary_failed"))
                        all_jobs.append(meta)
                except Exception:
                    pass
            elif folder.startswith("Meet-") or len(folder) == 8:
                all_jobs.append({
                    "job_id": folder,
                    "status": "completed",
                    "url": f"https://meet.google.com/{folder.split('-')[1]}" if folder.startswith("Meet-") else "Legacy",
                    "duration_seconds": 0,
                    "has_audio": os.path.exists(os.path.join(rec_dir, folder, "audio.wav")),
                    "has_captions": os.path.exists(os.path.join(rec_dir, folder, "captions.txt")),
                    "has_summary": os.path.exists(os.path.join(rec_dir, folder, "summary.md")),
                    "summary_failed": os.path.exists(os.path.join(rec_dir, folder, ".summary_failed"))
                })
                
    return all_jobs


@app.post("/stop-bot/{job_id}")
async def stop_bot(job_id: str):
    """Send stop signal to an active bot."""
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")

    bot = bots_db.get(job_id)
    if not bot:
        raise HTTPException(status_code=400, detail="No active bot for this job")

    bot.stop()
    return {"message": "Stop signal sent", "job_id": job_id}


@app.post("/summarize/{job_id}")
async def summarize_bot(job_id: str, background_tasks: BackgroundTasks):
    """Trigger the LLM 3-stage summarizer in the background."""
    rec_dir = os.path.join("recordings", job_id)
    if not os.path.exists(rec_dir):
        raise HTTPException(status_code=404, detail="Job recordings not found")
        
    fail_file = os.path.join(rec_dir, ".summary_failed")
    if os.path.exists(fail_file):
        try: os.remove(fail_file)
        except: pass
        
    loop = asyncio.get_event_loop()
    background_tasks.add_task(lambda: loop.create_task(asyncio.to_thread(generate_meeting_summary, job_id)))
    return {"status": "summarizing"}


@app.get("/download/{job_id}/{file_type}")
async def download_file(job_id: str, file_type: str):
    """Download audio, captions, or summary file."""
    allowed = {"audio": "audio.wav", "captions": "captions.txt", "summary": "summary.md"}
    if file_type not in allowed:
        raise HTTPException(status_code=400, detail="Invalid file type")

    filepath = os.path.join("recordings", job_id, allowed[file_type])
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(filepath, filename=f"MeetingBuddy_{job_id}_{allowed[file_type]}")


@app.get("/health")
async def health():
    return {"status": "ok", "active_bots": len(bots_db), "total_jobs": len(jobs_db)}


# ── Run : Directly running on aws no need to below commands

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)