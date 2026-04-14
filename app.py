"""
Meeting Buddy — FastAPI Backend
Minimal API for deploying, monitoring, and stopping the Meet bot.
"""

from fastapi import FastAPI, HTTPException, BackgroundTasks, Header, Depends
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
import subprocess
import uuid
import json
import os
import sqlite3
from dotenv import load_dotenv

load_dotenv()

# ── Database Init ──
DB_PATH = "database.db"

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS meetings (
                job_id TEXT PRIMARY KEY,
                user_email TEXT,
                url TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()

init_db()

from meet_bot import MeetBot
from summarizer import generate_meeting_summary

# ── App ────

app = FastAPI(title="Meeting Buddy API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── In-memory stores 

jobs_db: Dict[str, dict] = {}


# ── Models 

class MeetRequest(BaseModel):
    url: str


class JobResponse(BaseModel):
    job_id: str
    status: str
    message: str


# ── Background worker (Removed entirely; replaced by subprocess model) 


# ── Auth Dependency ──

ALLOWED_EMAILS_STR = os.environ.get("ALLOWED_EMAILS", "")
ALLOWED_EMAILS = [e.strip() for e in ALLOWED_EMAILS_STR.split(",") if e.strip()]

def verify_token(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        print(f"!!! Missing Authorization Header. Received: {authorization}", flush=True)
        raise HTTPException(status_code=401, detail="Unauthorized - Missing Bearer Token")
    
    token = authorization.split(" ")[1]
    try:
        # Pass the Google Client ID if available, otherwise just verify signature
        client_id = os.environ.get("GOOGLE_CLIENT_ID")
        idinfo = id_token.verify_oauth2_token(token, google_requests.Request(), client_id, clock_skew_in_seconds=60)
        email = idinfo.get("email")
        # Whitelist intentionally removed: ANY valid Google account is now permitted.
        return email
    except Exception as e:
        import traceback
        with open("oauth_error.log", "w") as f:
            f.write("OAuth Validation Failed:\n")
            f.write(f"Error: {str(e)}\n\n")
            f.write(traceback.format_exc())
            f.write(f"\nToken len: {len(token)}\n")
        raise HTTPException(status_code=401, detail=f"Google OAuth Error: {str(e)}")

# ── Endpoints ───────

@app.post("/deploy-bot", response_model=JobResponse)
async def deploy_bot(request: MeetRequest, background_tasks: BackgroundTasks, user_email: str = Depends(verify_token)):
    """Start the bot for a given Google Meet URL."""
    if "meet.google.com" not in request.url:
        raise HTTPException(status_code=400, detail="Invalid Google Meet URL")

    meet_id = request.url.split('meet.google.com/')[-1].split('?')[0].strip('/')
    if not meet_id:
        meet_id = str(uuid.uuid4())[:8]
        
    dt_str = datetime.now().strftime("%d-%m-%y-%H-%M-%S")
    job_id = f"Meet-{meet_id}-{dt_str}"

    process = subprocess.Popen([
        "/home/ubuntu/Meeting-Buddy/.venv/bin/python",
        "worker_bot.py",
        job_id,
        request.url
    ])

    jobs_db[job_id] = {
        "job_id": job_id,
        "status": "running",
        "url": request.url,
        "process": process,
        "result": None,
        "error": None,
        "user_email": user_email
    }
    
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO meetings (job_id, user_email, url) VALUES (?, ?, ?)",
            (job_id, user_email, request.url)
        )
        conn.commit()

    

    return JobResponse(
        job_id=job_id, status="running", message="Meeting Buddy is deploying!"
    )


def check_ownership(job_id: str, user_email: str):
    """Enforce strict multi-tenant tenant isolation for existing database rows."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT user_email FROM meetings WHERE job_id = ?", (job_id,))
        row = cur.fetchone()
        if not row or row["user_email"] != user_email:
            raise HTTPException(status_code=403, detail="You do not have permission to access this meeting record")

@app.get("/job-status/{job_id}")
async def get_job_status(job_id: str, user_email: str = Depends(verify_token)):
    """Get current job status. Includes live captions if bot is active."""
    check_ownership(job_id, user_email)
    
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found in memory")

    job = dict(jobs_db[job_id])
    process = job.get("process")

    # Mirror process state
    if process:
        if process.poll() is None:
            job["status"] = "running"
        elif job.get("status") in ["running", "pending"]:
            job["status"] = "finished"
    
    if job["status"] in ["finished", "failed", "stopped"]:
        job.pop("process", None)
    # Stream live captions from disk since worker is fully isolated
    job["live_captions"] = []
    job["captions_count"] = 0
    cap_path = os.path.join("recordings", job_id, "captions.txt")
    if os.path.exists(cap_path):
        try:
            with open(cap_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
                job["captions_count"] = len(lines)
                job["live_captions"] = lines[-20:]
        except Exception:
            pass

    # Ensure we don't accidentally leak the subprocess object via JSON
    job.pop("process", None)
    return job


@app.get("/jobs")
async def get_all_jobs(user_email: str = Depends(verify_token)):
    """Get all historical and active jobs securely attached to the user."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT job_id, url FROM meetings WHERE user_email = ?", (user_email,))
        user_rows = cur.fetchall()
        
    user_jobs = {row["job_id"]: dict(row) for row in user_rows}
    all_jobs = []
    rec_dir = "recordings"
    
    for jid, db_meta in user_jobs.items():
        job_copy = {
            "job_id": jid,
            "url": db_meta["url"],
            "status": "completed", 
            "duration_seconds": 0,
            "has_audio": False,
            "has_captions": False,
            "has_summary": False,
            "summary_failed": False,
            "captions_count": 0
        }
        
        # Fold in live memory state if active
        if jid in jobs_db:
            mem_job = jobs_db[jid]
            process = mem_job.get("process")
            if process and process.poll() is None:
                job_copy["status"] = "running"
                cap_path = os.path.join(rec_dir, jid, "captions.txt")
                if os.path.exists(cap_path):
                    with open(cap_path, "r", encoding="utf-8") as f:
                        job_copy["captions_count"] = sum(1 for line in f if line.strip())
            else:
                res = mem_job.get("result", {})
                if res:
                    job_copy["status"] = mem_job.get("status", "completed")
                    job_copy["duration_seconds"] = res.get("duration_seconds", 0)
        else:
            # Fold in disk metadata.json if concluded
            meta_path = os.path.join(rec_dir, jid, "metadata.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                        job_copy["duration_seconds"] = meta.get("duration_seconds", 0)
                        job_copy["captions_count"] = meta.get("captions_count", 0)
                        job_copy["status"] = meta.get("status", "completed")
                except Exception: pass
            else:
                # If it crashed instantly before writing metadata, mark failed gracefully
                if not os.path.exists(os.path.join(rec_dir, jid)):
                    job_copy["status"] = "failed"
                    
        # Verify physical disk payloads
        folder = os.path.join(rec_dir, jid)
        job_copy["has_audio"] = os.path.exists(os.path.join(folder, "audio.wav"))
        job_copy["has_captions"] = os.path.exists(os.path.join(folder, "captions.txt"))
        job_copy["has_summary"] = os.path.exists(os.path.join(folder, "summary.md"))
        job_copy["summary_failed"] = os.path.exists(os.path.join(folder, ".summary_failed"))
        all_jobs.append(job_copy)

    return all_jobs


@app.delete("/jobs/failed")
async def delete_failed_jobs(user_email: str = Depends(verify_token)):
    """Wipe out all definitively failed jobs cleanly from the database."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute("SELECT job_id FROM meetings WHERE user_email = ?", (user_email,))
        user_rows = cur.fetchall()
        
    user_job_ids = [row["job_id"] for row in user_rows]
    rec_dir = "recordings"
    
    deleted_count = 0
    with sqlite3.connect(DB_PATH) as conn:
        for jid in user_job_ids:
            # We ONLY delete logically aborted/failed ones that don't have good data
            meta_path = os.path.join(rec_dir, jid, "metadata.json")
            status = "failed"
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r") as f:
                        meta = json.load(f)
                        status = meta.get("status", "completed")
                except Exception: pass
            else:
                if jid in jobs_db:
                    status = jobs_db[jid].get("status", "failed")
            
            if status == "failed":
                conn.execute("DELETE FROM meetings WHERE job_id = ?", (jid,))
                if jid in jobs_db: del jobs_db[jid]
                deleted_count += 1
                
        conn.commit()
    
    return {"message": f"Successfully deleted {deleted_count} failed meetings.", "deleted": deleted_count}


@app.post("/stop-bot/{job_id}")
async def stop_bot(job_id: str, user_email: str = Depends(verify_token)):
    """Send stop signal to an active bot."""
    check_ownership(job_id, user_email)
    if job_id not in jobs_db:
        raise HTTPException(status_code=404, detail="Job not found")

    job = jobs_db.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    process = job.get("process")

    if process and process.poll() is None:
        process.terminate()
        job["status"] = "stopped"
        return {"message": "Bot stopped", "job_id": job_id}

    return {"message": "Bot already finished", "job_id": job_id}


@app.post("/summarize/{job_id}")
async def summarize_bot(job_id: str, background_tasks: BackgroundTasks, user_email: str = Depends(verify_token)):
    """Trigger the LLM 3-stage summarizer in the background."""
    check_ownership(job_id, user_email)
    rec_dir = os.path.join("recordings", job_id)
    if not os.path.exists(rec_dir):
        raise HTTPException(status_code=404, detail="Job recordings not found")
        
    fail_file = os.path.join(rec_dir, ".summary_failed")
    if os.path.exists(fail_file):
        try: os.remove(fail_file)
        except: pass
        
    background_tasks.add_task(generate_meeting_summary, job_id)
    return {"status": "summarizing"}


@app.get("/download/{job_id}/{file_type}")
async def download_file(job_id: str, file_type: str, user_email: str = Depends(verify_token)):
    """Download audio, captions, or summary file securely natively."""
    check_ownership(job_id, user_email)
    allowed = {"audio": "audio.wav", "captions": "captions.txt", "summary": "summary.md"}
    if file_type not in allowed:
        raise HTTPException(status_code=400, detail="Invalid file type")

    filepath = os.path.join("recordings", job_id, allowed[file_type])
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(filepath, filename=f"MeetingBuddy_{job_id}_{allowed[file_type]}")


@app.get("/health")
async def health():
    return {"status": "ok", "total_jobs": len(jobs_db)}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)