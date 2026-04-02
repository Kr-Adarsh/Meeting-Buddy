import os
import json
import time
import requests
from dotenv import load_dotenv

# Load env early
load_dotenv()

MISTRAL_API_KEY = os.environ.get("MISTRAL_API_KEY")

def mark_failed(job_dir: str):
    with open(os.path.join(job_dir, ".summary_failed"), "w") as f:
        f.write("failed")

def generate_meeting_summary(job_id: str):
    """
    Executes the 2-Stage Mistral Summarization pipeline.
    Stage 1: Voxtral Audio Transcription & Diarization
    Stage 2: Mistral Small Layout & Naming Alignment
    """
    print(f"[Summarizer] Starting Mistral pipeline for {job_id}")
    job_dir = os.path.join("recordings", job_id)
    if not os.path.exists(job_dir):
        print(f"[Summarizer] Error: Folder {job_dir} not found.")
        return

    audio_path = os.path.join(job_dir, "audio.wav")
    captions_path = os.path.join(job_dir, "captions.txt")
    
    if not MISTRAL_API_KEY:
        print("[Summarizer] Error: MISTRAL_API_KEY not found in environment.")
        mark_failed(job_dir)
        return

    # Read Captions
    captions_text = ""
    if os.path.exists(captions_path):
        with open(captions_path, "r", encoding="utf-8") as f:
            captions_text = f.read()

    # ==========================================
    # STAGE 1: Audio Transcription & Diarization
    # ==========================================
    raw_diarized_transcript = ""
    
    if os.path.exists(audio_path):
        print("[Summarizer] Stage 1: Processing Audio via Voxtral API...")
        url_audio = "https://api.mistral.ai/v1/audio/transcriptions"
        
        try:
            with open(audio_path, "rb") as f:
                files = {"file": ("audio.wav", f, "audio/wav")}
                data = {
                    "model": "voxtral-mini-latest",
                    "diarize": "true",
                    "timestamp_granularities": "segment" 
                }
                headers = {"Authorization": f"Bearer {MISTRAL_API_KEY}"}
                
                response = requests.post(url_audio, headers=headers, files=files, data=data, timeout=300)
                
            if response.status_code == 200:
                transcript_data = response.json()
                segments = transcript_data.get("segments", [])
                
                for seg in segments:
                    speaker = seg.get("speaker", "Unknown Speaker") 
                    text = seg.get("text", "").strip()
                    raw_diarized_transcript += f"[{speaker}]: {text}\n"
                    
                # Save raw diarization for debugging
                with open(os.path.join(job_dir, "transcript_aligned.txt"), "w", encoding="utf-8") as f:
                    f.write(raw_diarized_transcript)
                
                print("[Summarizer] Stage 1 Complete. Got Diarized Audio Transcript.")
            else:
                print(f"[Summarizer] Stage 1 Error ({response.status_code}): {response.text}")
                print("[Summarizer] Falling back to text captions only.")
                raw_diarized_transcript = captions_text
                
        except Exception as e:
            print(f"[Summarizer] Stage 1 Connection Error: {e}")
            raw_diarized_transcript = captions_text
    else:
        print("[Summarizer] Stage 1 Skipped: No audio file. Proceeding with text only.")
        raw_diarized_transcript = captions_text

    # ==========================================
    # STAGE 2: Alignment & Summarization
    # ==========================================
    print("[Summarizer] Stage 2: Cross-referencing names and generating Markdown Notes...")

    if not raw_diarized_transcript.strip() and not captions_text.strip():
        print("[Summarizer] Critical Error: Cannot generate summary, no text gathered at all.")
        mark_failed(job_dir)
        return

    system_prompt = """You are an expert Executive Assistant. I will provide you with two pieces of context from a meeting:
1. A 'Raw Audio Transcript' that is highly accurate but uses generic speaker tags (e.g., SPEAKER_00).
2. A 'Captions File' that is less accurate but contains the real names of the participants.

Your task:
0. Tone: The meeting summary/Notes should be professional without with to the point things, dicussed in the meet. 
1. Alignment: Cross-reference the dialogue to figure out who the generic speakers actually are based on the Captions File.
2. Summarization: Output a beautifully formatted, highly detailed Meeting Notes document in Rendered Markdown (.md)(give direct md code, do NOT wrap in ```markdown ```). 
Ensure all dialogue, action items, and key decisions are correctly attributed to the real names of the people speaking."""

    user_prompt = f"""
### 1. Raw Audio Transcript:
{raw_diarized_transcript}

### 2. Captions File:
{captions_text}
"""

    url_chat = "https://api.mistral.ai/v1/chat/completions"
    chat_payload = {
        "model": "mistral-small-latest",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
    }
    chat_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {MISTRAL_API_KEY}"
    }

    for attempt in range(1, 4):
        try:
            chat_response = requests.post(url_chat, headers=chat_headers, json=chat_payload, timeout=60)
            
            if chat_response.status_code == 200:
                final_notes = chat_response.json()['choices'][0]['message']['content']
                
                # Verify and enforce payload strip (Mistral sometimes includes markdown blocks anyway)
                if final_notes.startswith("```markdown"):
                    final_notes = final_notes[11:].strip()
                elif final_notes.startswith("```md"):
                    final_notes = final_notes[5:].strip()
                if final_notes.endswith("```"):
                    final_notes = final_notes[:-3].strip()

                with open(os.path.join(job_dir, "summary.md"), "w", encoding="utf-8") as f:
                    f.write(final_notes)
                    
                print("[Summarizer] Stage 2 Complete! summary.md created.")
                break
            elif chat_response.status_code == 429:
                wait_time = 2 ** attempt
                print(f"[Summarizer] Rate limited in Stage 2. Waiting {wait_time}s...")
                time.sleep(wait_time)
            else:
                print(f"[Summarizer] Stage 2 Error ({chat_response.status_code}): {chat_response.text}")
                mark_failed(job_dir)
                break
                
        except Exception as e:
            print(f"[Summarizer] Stage 2 Connection Error: {e}")
            mark_failed(job_dir)
            time.sleep(2)
