import { useState, useEffect, useRef } from 'react';
import {
  Play, Loader2, Download, Clock, Mic, Square,
  AlertCircle, MessageSquare, Bot, CheckCircle2, RotateCcw, X, Eye
} from 'lucide-react';
import './App.css';

const API_URL = "http://18.220.20.49:8000";

function App() {
  const [meetLink, setMeetLink] = useState('');
  const [error, setError] = useState(null);

  // All active and historical jobs
  const [jobs, setJobs] = useState([]);
  const [loadingDeploy, setLoadingDeploy] = useState(false);

  // Inline Peek States
  const [peekJobId, setPeekJobId] = useState(null);
  const [peekCaptions, setPeekCaptions] = useState([]);
  const [isSummarizing, setIsSummarizing] = useState({});

  // Clear isSummarizing lock if the backend confirmed it finished (either success or failed)
  useEffect(() => {
    let changed = false;
    const nextObj = { ...isSummarizing };
    jobs.forEach(j => {
      if ((j.has_summary || j.summary_failed) && nextObj[j.job_id]) {
        nextObj[j.job_id] = false;
        changed = true;
      }
    });
    if (changed) setIsSummarizing(nextObj);
  }, [jobs, isSummarizing]);

  // Fetch Jobs Loop
  useEffect(() => {
    const fetchJobs = async () => {
      try {
        const res = await fetch(`${API_URL}/jobs`);
        if (res.ok) {
          let data = await res.json();

          // Parse actual timestamps for accurate sorting instead of basic localeCompare
          const parseTime = (id) => {
            if (!id.startsWith("Meet-")) return 0;
            const p = id.split('-');
            if (p.length < 10) return 0;
            // Meet-id-dd-mm-yy-H-M-S -> 20yy-mm-ddTHH:MM:SS
            return new Date(`20${p[6]}-${p[5]}-${p[4]}T${p[7]}:${p[8]}:${p[9]}`).getTime();
          };

          data.sort((a, b) => parseTime(a.job_id) - parseTime(b.job_id));

          // Assign sequential IDs chronologically
          data = data.map((job, index) => ({
            ...job,
            serialId: `Session #${index + 1}`
          }));

          // Render newest at top
          data.reverse();
          setJobs(data);
        }
      } catch (err) {
        console.error("Failed to fetch jobs");
      }
    };

    fetchJobs();
    const iv = setInterval(fetchJobs, 3000);
    return () => clearInterval(iv);
  }, []);

  // Poll Peek Captions independently if peeking at an active job
  useEffect(() => {
    let iv;

    // Only poll if there's an active peek AND the job is NOT completed
    const targetJob = jobs.find(j => j.job_id === peekJobId);
    const isLive = targetJob && targetJob.status !== 'completed' && targetJob.status !== 'failed';

    if (peekJobId && isLive) {
      const getPeekStatus = async () => {
        try {
          const sr = await fetch(`${API_URL}/job-status/${peekJobId}`);
          if (!sr.ok) return;
          const sdata = await sr.json();
          if (sdata.status === 'completed') {
            // It finished while we were peeking! Need to fetch final static captions.
            const fr = await fetch(`${API_URL}/download/${peekJobId}/captions`);
            if (fr.ok) {
              const staticText = await fr.text();
              const lines = staticText.split('\n').filter(l => l.trim().length > 0);
              const formatted = lines.map(l => {
                const parts = l.split('] ');
                if (parts.length > 1) {
                  return { time: parts[0] + ']', text: parts.slice(1).join('] ') };
                }
                return { time: '', text: l };
              });
              setPeekCaptions(formatted);
            }
          } else {
            setPeekCaptions(sdata.captions || []);
          }
        } catch (e) {
          console.error(e);
        }
      };

      getPeekStatus();
      iv = setInterval(getPeekStatus, 2000);
    }
    return () => { if (iv) clearInterval(iv); };
  }, [peekJobId, jobs]);

  const captionsEndRef = useRef(null);
  useEffect(() => {
    if (captionsEndRef.current && captionsEndRef.current.parentElement) {
      const parent = captionsEndRef.current.parentElement;
      parent.scrollTop = parent.scrollHeight;
    }
  }, [peekCaptions]);

  // ── Deploy bot ──────────────────────────
  const handleDeploy = async (e) => {
    e.preventDefault();
    if (!meetLink.trim()) return;

    setError(null);
    setLoadingDeploy(true);

    try {
      const res = await fetch(`${API_URL}/deploy-bot`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: meetLink.trim() }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to deploy bot');
      }
      // Instantly clear and get ready for another
      setMeetLink('');
    } catch (err) {
      setError(err.message);
    } finally {
      setLoadingDeploy(false);
    }
  };

  // ── Summarize ────────────────────────────
  const handleSummarize = async (jobId) => {
    setIsSummarizing(prev => ({ ...prev, [jobId]: true }));
    try {
      await fetch(`${API_URL}/summarize/${jobId}`, { method: 'POST' });
    } catch (e) {
      console.error('Summarize error:', e);
      setIsSummarizing(prev => ({ ...prev, [jobId]: false }));
    }
  };

  // ── Stop bot ────────────────────────────
  const handleStop = async (jobId) => {
    try {
      if (peekJobId === jobId) setPeekJobId(null); // Auto-collapse peek
      await fetch(`${API_URL}/stop-bot/${jobId}`, { method: 'POST' });
    } catch (e) {
      console.error('Stop error:', e);
    }
  };

  // ── Download ────────────────────────────
  const download = (jobId, type) => {
    window.open(`${API_URL}/download/${jobId}/${type}`, '_blank');
  };

  const fmt = (s) => {
    const m = Math.floor(s / 60);
    return `${m}:${(s % 60).toString().padStart(2, '0')}`;
  };

  const parseJobInfo = (jobId, duration = 0) => {
    if (!jobId.startsWith("Meet-")) return { title: jobId, start: null, end: null };
    const parts = jobId.split('-');
    if (parts.length >= 10) {
      const id = `${parts[1]}-${parts[2]}-${parts[3]}`; // The exact Meet URL hash
      const date = `${parts[4]}-${parts[5]}-20${parts[6]}`; // dd-mm-20yy
      let hr = parseInt(parts[7], 10);
      let mn = parseInt(parts[8], 10);
      let sec = parseInt(parts[9], 10);

      const ampm = hr >= 12 ? 'PM' : 'AM';
      const startHr = hr % 12 || 12;
      const startStr = `${startHr}:${parts[8]}${ampm}`;

      let endStr = null;
      if (duration > 0) {
        const endTotalSec = hr * 3600 + mn * 60 + sec + duration;
        const eHr = Math.floor(endTotalSec / 3600) % 24;
        const eMn = Math.floor((endTotalSec % 3600) / 60);
        const eAmpm = eHr >= 12 ? 'PM' : 'AM';
        const endHr = eHr % 12 || 12;
        endStr = `${endHr}:${eMn.toString().padStart(2, '0')}${eAmpm}`;
      }

      return {
        title: `[${date}] Recorded Meeting`,
        subtitle: `URL: https://meet.google.com/${id}`,
        start: startStr,
        end: endStr
      };
    }
    return { title: jobId, subtitle: '', start: null, end: null };
  };

  const togglePeek = async (job) => {
    if (peekJobId === job.job_id) {
      setPeekJobId(null);
      setPeekCaptions([]);
    } else {
      setPeekJobId(job.job_id);
      if (job.status === 'completed' && job.has_captions) {
        // Fetch static file if completed
        try {
          const res = await fetch(`${API_URL}/download/${job.job_id}/captions`);
          if (res.ok) {
            const text = await res.text();
            const lines = text.split('\\n').filter(l => l.trim().length > 0).map(line => {
              const match = line.match(/^\[(.*?)\] (.*?): (.*)$/);
              if (match) return { timestamp: match[1], speaker: match[2], text: match[3] };
              return { text: line };
            });
            setPeekCaptions(lines);
          }
        } catch (e) { }
      } else {
        setPeekCaptions([]);
      }
    }
  };

  return (
    <div className="app-container">
      {/* Header */}
      <header className="header">
        <div className="logo-section">
          <div className="logo-icon"><Bot size={26} /></div>
          <div>
            <h1>Meeting Buddy</h1>
            <p className="subtitle">Record & capture your Google Meet sessions</p>
          </div>
        </div>
      </header>

      <main className="main-content">
        {/* Error */}
        {error && (
          <div className="error-banner">
            <AlertCircle size={18} />
            <span>{error}</span>
            <button onClick={() => setError(null)} className="error-close">×</button>
          </div>
        )}

        {/* Deploy Form */}
        <section className="deploy-section">
          <form onSubmit={handleDeploy} className="meet-form">
            <div className="input-wrapper">
              <Mic className="input-icon" size={20} />
              <input
                type="url"
                placeholder="Paste your Google Meet link here..."
                value={meetLink}
                onChange={(e) => setMeetLink(e.target.value)}
                required
                disabled={loadingDeploy}
                className="meet-input"
                id="meet-link-input"
              />
            </div>
            <button
              type="submit"
              className={`deploy-btn ${loadingDeploy ? 'disabled' : ''}`}
              disabled={loadingDeploy}
              id="deploy-btn"
            >
              {loadingDeploy ? <Loader2 size={18} className="spinner" /> : <Play size={18} />}
              <span>Deploy Bot</span>
            </button>
          </form>
        </section>

        {/* All Meets Matrix */}
        <section className="meets-list-section">
          <div className="section-header">
            <h3>All Meets</h3>
          </div>

          <div className="meets-grid">
            {jobs.length === 0 && <p className="no-data">No meetings recorded yet.</p>}

            {jobs.map((job) => {
              const isActive = job.status === 'joining' || job.status === 'pending' || job.status === 'recording';
              const info = parseJobInfo(job.job_id, job.duration_seconds);
              const isPeeking = peekJobId === job.job_id;

              return (
                <div key={job.job_id} className={`meet-card ${isActive ? 'active-card' : ''}`}>
                  <div className="meet-card-header">
                    <div className="meet-card-title">
                      {isActive && <div className="pulse-dot" style={{ marginRight: '6px' }}></div>}
                      <strong>{job.serialId || info.title}</strong>
                      <span style={{ color: 'var(--text-muted)', fontSize: '0.9em', marginLeft: '8px' }}>{info.title}</span>
                    </div>
                    <span className={`status-badge ${job.status}`}>
                      {job.status.toUpperCase()}
                    </span>
                  </div>

                  <div className="meet-card-body">
                    {(!info.start) ? (
                      <p className="meet-url">{job.url}</p>
                    ) : (
                      <p className="meet-url" style={{ marginBottom: '-4px' }}>{info.subtitle}</p>
                    )}

                    {info.start && (
                      <div className="meet-stats" style={{ color: 'var(--text)', fontWeight: 500 }}>
                        <span>Start: {info.start}</span>
                        {info.end && <span> | End: {info.end}</span>}
                        {job.duration_seconds > 0 && <span style={{ color: 'var(--text-muted)', fontWeight: 400 }}>({fmt(job.duration_seconds)})</span>}
                      </div>
                    )}

                    <div className="meet-actions">
                      {isActive && (
                        <button onClick={() => handleStop(job.job_id)} className="stop-btn">
                          <Square size={14} /> Stop
                        </button>
                      )}

                      {((isActive && (job.captions_count > 0 || peekCaptions.length > 0)) || (!isActive && job.has_captions)) && (
                        <button onClick={() => togglePeek(job)} className="peek-btn" style={isPeeking ? { background: 'rgba(139, 92, 246, 0.15)', borderColor: 'var(--primary)' } : {}}>
                          {isPeeking ? <X size={14} /> : <Eye size={14} />} {isPeeking ? "Close Peek" : "Peek"}
                        </button>
                      )}

                      {!isActive && job.status === 'completed' && (
                        <>
                          {job.has_audio && (
                            <button onClick={() => download(job.job_id, 'audio')} className="raw-download-btn">Audio</button>
                          )}
                          {job.has_captions && (
                            <button onClick={() => download(job.job_id, 'captions')} className="raw-download-btn">Captions</button>
                          )}
                          {job.has_summary ? (
                            <button onClick={() => download(job.job_id, 'summary')} className="raw-download-btn" style={{ background: 'var(--success)', color: '#fff', border: 'none' }}>Download Summary</button>
                          ) : isSummarizing[job.job_id] ? (
                            <button disabled className="raw-download-btn" style={{ opacity: 0.6 }}>Generating...</button>
                          ) : job.summary_failed ? (
                            <button onClick={() => handleSummarize(job.job_id)} className="raw-download-btn" style={{ background: 'var(--error)', color: '#fff', border: 'none' }}>Failed - Retry</button>
                          ) : (job.has_audio || job.has_captions) ? (
                            <button onClick={() => handleSummarize(job.job_id)} className="raw-download-btn" style={{ background: 'var(--primary)', color: '#fff', border: 'none' }}>Generate Notes</button>
                          ) : null}
                        </>
                      )}
                    </div>
                  </div>

                  {/* Inline Peek Accordion */}
                  {isPeeking && (
                    <div className="inline-peek-drawer">
                      <div className="captions-content" style={{ height: '250px', background: 'var(--background)', marginTop: '1rem', borderRadius: 'var(--radius-sm)' }}>
                        {peekCaptions.length === 0 ? (
                          <div className="empty-state">
                            {isActive ? <Loader2 size={20} className="spinner" /> : <Eye size={20} />}
                            <p>{isActive ? "Listening for speech..." : "No captions extracted."}</p>
                          </div>
                        ) : (
                          peekCaptions.map((cap, i) => (
                            <div key={i} className="caption-line" style={{ padding: '0.25rem 0' }}>
                              {cap.timestamp && (
                                <span className="caption-time">
                                  {new Date(cap.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                                </span>
                              )}
                              {cap.speaker && <strong style={{ color: '#8b5cf6', marginRight: '6px' }}>{cap.speaker}:</strong>}
                              <span className="caption-text">{cap.text}</span>
                            </div>
                          ))
                        )}
                        <div ref={captionsEndRef} />
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>

      </main>
    </div>
  );
}

export default App;