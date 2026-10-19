import React, { useState, useEffect, useRef } from 'react';

export default function App() {
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(false);
  const [instruction, setInstruction] = useState('');
  const [history, setHistory] = useState([]);
  const [pendingClarification, setPendingClarification] = useState(null);
  const [renderJob, setRenderJob] = useState(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [uploadProgress, setUploadProgress] = useState(null);
  const videoRef = useRef(null);

  // Suggested prompt steps from worked example
  const workedSteps = [
    "Add a caption 'Welcome!' when I say welcome to the channel",
    "Cut the first 30 seconds",
    "Remove the part about pricing",
    "Speed up from 1:00 to 1:30 by 2x",
    "Actually, put the intro back",
    "Undo",
    "Make that caption red"
  ];

  // Load existing or demo project
  useEffect(() => {
    fetch('/api/v1/projects')
      .then(r => r.json())
      .then(projs => {
        if (projs && projs.length > 0) {
          loadProject(projs[0].project_id);
        }
      })
      .catch(console.error);
  }, []);

  const loadProject = async (id) => {
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/projects/${id}`);
      const data = await res.json();
      setProject(data);
      if (data.pending_clarification) {
        setPendingClarification(data.pending_clarification);
      }
    } catch (err) {
      console.error('Failed to load project', err);
    } finally {
      setLoading(false);
    }
  };

  const [sttTask, setSttTask] = useState('transcribe'); // 'transcribe' or 'translate'

  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // Validate size (500MB max) & extension
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    if (!['.mp4', '.mov', '.mkv'].includes(ext)) {
      alert(`Invalid file extension ${ext}. Only MP4, MOV, and MKV are permitted.`);
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      alert(`File size exceeds 500MB limit.`);
      return;
    }

    setLoading(true);
    setUploadProgress(`Uploading video & running ${sttTask}...`);
    const formData = new FormData();
    formData.append('file', file);
    formData.append('task', sttTask);

    try {
      const res = await fetch('/api/v1/projects/upload', {
        method: 'POST',
        headers: { 'Idempotency-Key': `upload_${Date.now()}` },
        body: formData,
      });
      const data = await res.json();
      if (res.ok) {
        await loadProject(data.project_id);
      } else {
        alert(data.error?.message || 'Upload failed');
      }
    } catch (err) {
      alert('Network error during upload');
    } finally {
      setLoading(false);
      setUploadProgress(null);
    }
  };

  const executeInstruction = async (textToSend) => {
    const promptText = textToSend || instruction;
    if (!promptText.trim() || !project) return;

    setLoading(true);
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/instructions`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `inst_${Date.now()}`
        },
        body: JSON.stringify({ instruction: promptText }),
      });
      const result = await res.json();

      setHistory(prev => [{ prompt: promptText, result }, ...prev]);
      setInstruction('');

      if (result.status === 'needs_clarification') {
        setPendingClarification({
          question: result.question,
          options: result.options,
          instruction: promptText,
          keyword: promptText.split(' ').pop(),
        });
      } else {
        setPendingClarification(null);
      }

      // Refresh project timeline and overlays
      await loadProject(project.project_id);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const triggerRender = async () => {
    if (!project) return;
    setLoading(true);
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/render`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `render_${Date.now()}`
        },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      setRenderJob(data);

      // Poll job status
      const pollInterval = setInterval(async () => {
        try {
          const pollRes = await fetch(`/api/v1/jobs/${data.job_id}`);
          const jobData = await pollRes.json();
          setRenderJob(jobData);
          if (jobData.status === 'succeeded' || jobData.status === 'failed') {
            clearInterval(pollInterval);
          }
        } catch (e) {
          clearInterval(pollInterval);
        }
      }, 1500);

    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // Check active overlay at current playback time
  const activeOverlay = project?.projected_overlays?.find(
    ov => currentTime >= ov.timeline_start && currentTime <= ov.timeline_end
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      {/* Top Navbar */}
      <header style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '12px 24px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'rgba(16, 22, 38, 0.95)',
        backdropFilter: 'blur(10px)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          <div style={{
            background: 'linear-gradient(135deg, #6366f1, #a855f7)',
            width: '32px',
            height: '32px',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: '800',
            fontSize: '18px',
          }}>
            I
          </div>
          <div>
            <h1 style={{ fontSize: '1.15rem', fontWeight: '700', letterSpacing: '-0.02em' }}>IncuBrix Editor</h1>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Prompt-Based Non-Destructive Video Engine</p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '16px' }}>
          {project && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', fontSize: '0.8rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>Policy: <strong style={{ color: '#818cf8' }}>{project.timeline.time_policy}</strong></span>
              <span style={{ color: 'var(--text-muted)' }}>Duration: <strong style={{ color: '#38bdf8' }}>{project.timeline.duration_s.toFixed(2)}s</strong></span>
              <span style={{
                background: 'rgba(99, 102, 241, 0.1)',
                padding: '4px 8px',
                borderRadius: '6px',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.75rem',
                color: '#a5b4fc',
              }}>
                Hash: {project.timeline.timeline_hash.substring(0, 8)}...
              </span>
            </div>
          )}
          <select
            value={sttTask}
            onChange={(e) => setSttTask(e.target.value)}
            style={{
              background: 'rgba(30, 41, 59, 0.8)',
              color: '#fff',
              border: '1px solid var(--border-subtle)',
              borderRadius: '8px',
              padding: '8px 10px',
              fontSize: '0.8rem',
              outline: 'none',
              cursor: 'pointer'
            }}
          >
            <option value="transcribe">Task: Transcribe Speech</option>
            <option value="translate">Task: Translate to English</option>
          </select>
          <label className="btn-secondary" style={{ cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: '6px' }}>
            <span>Upload Media</span>
            <input type="file" accept=".mp4,.mov,.mkv" onChange={handleFileUpload} style={{ display: 'none' }} />
          </label>
          <button className="btn-primary" onClick={triggerRender} disabled={!project || loading}>
            Render Final
          </button>
        </div>
      </header>

      {/* Main Workspace */}
      <main style={{ display: 'grid', gridTemplateColumns: '1fr 440px', flex: 1, overflow: 'hidden' }}>
        
        {/* Left: Video Player, Timeline & Transcript */}
        <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border-subtle)', overflowY: 'auto' }}>
          
          {/* Video Preview Card */}
          <div style={{ padding: '20px', display: 'flex', justifyContent: 'center', background: '#070a10' }}>
            <div style={{ position: 'relative', width: '100%', maxWidth: '780px', aspectRatio: '16/9', background: '#000', borderRadius: '12px', overflow: 'hidden', boxShadow: '0 20px 40px rgba(0,0,0,0.6)' }}>
              {project ? (
                <video
                  ref={videoRef}
                  src={`/api/v1/media/${project.source_media.filename}`}
                  controls
                  onTimeUpdate={(e) => setCurrentTime(e.target.currentTime)}
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                />
              ) : (
                <div style={{ height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'var(--text-muted)' }}>
                  Upload a video to begin editing
                </div>
              )}

              {/* Dynamic Content-Anchored Overlay Burn-in Simulation */}
              {activeOverlay && (
                <div style={{
                  position: 'absolute',
                  bottom: activeOverlay.position === 'bottom' ? '30px' : 'auto',
                  top: activeOverlay.position === 'top' ? '30px' : 'auto',
                  left: '50%',
                  transform: 'translateX(-50%)',
                  background: 'rgba(0, 0, 0, 0.75)',
                  backdropFilter: 'blur(4px)',
                  padding: '8px 18px',
                  borderRadius: '8px',
                  color: activeOverlay.color || '#FFFFFF',
                  fontSize: `${activeOverlay.font_size ? activeOverlay.font_size * 0.75 : 24}px`,
                  fontWeight: '700',
                  textAlign: 'center',
                  textShadow: '0 2px 4px rgba(0,0,0,0.8)',
                  pointerEvents: 'none',
                  animation: 'fadeIn 0.2s ease-in',
                }}>
                  {activeOverlay.content}
                </div>
              )}
            </div>
          </div>

          {/* Visual Timeline Section */}
          <div style={{ padding: '16px 24px', borderTop: '1px solid var(--border-subtle)', background: 'rgba(15, 23, 42, 0.4)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <h3 style={{ fontSize: '0.85rem', fontWeight: '600', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Timeline Track Composition
              </h3>
              <span style={{ fontSize: '0.75rem', color: 'var(--text-faint)' }}>
                {project?.timeline?.segments?.length || 0} active segments
              </span>
            </div>

            {/* Segment Track Bar */}
            <div style={{
              height: '38px',
              background: '#090d16',
              borderRadius: '8px',
              border: '1px solid #1e293b',
              display: 'flex',
              overflow: 'hidden',
              padding: '2px',
              gap: '2px',
            }}>
              {project?.timeline?.segments?.map((seg, i) => {
                const totalDur = project.timeline.duration_s || 1;
                const widthPercent = (seg.timeline_duration / totalDur) * 100;
                return (
                  <div
                    key={seg.segment_id}
                    title={`Seg ${i+1}: [${seg.source_start.toFixed(1)}s - ${seg.source_end.toFixed(1)}s] @ ${seg.speed}x`}
                    style={{
                      width: `${widthPercent}%`,
                      height: '100%',
                      background: seg.speed > 1.0 ? 'linear-gradient(135deg, #818cf8, #6366f1)' : 'linear-gradient(135deg, #3b82f6, #1d4ed8)',
                      borderRadius: '4px',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '0.7rem',
                      fontWeight: '600',
                      color: '#ffffff',
                      textShadow: '0 1px 2px rgba(0,0,0,0.5)',
                      cursor: 'pointer',
                    }}
                    onClick={() => {
                      if (videoRef.current) videoRef.current.currentTime = seg.source_start;
                    }}
                  >
                    {seg.speed > 1.0 ? `${seg.speed}x` : `S${i+1}`}
                  </div>
                );
              })}
            </div>

            {/* Overlays / Captions Track */}
            {project?.projected_overlays && project.projected_overlays.length > 0 && (
              <div style={{ marginTop: '8px', height: '26px', background: '#090d16', borderRadius: '6px', border: '1px solid #1e293b', position: 'relative', overflow: 'hidden' }}>
                {project.projected_overlays.map((ov) => {
                  const totalDur = project.timeline.duration_s || 1;
                  const left = (ov.timeline_start / totalDur) * 100;
                  const width = ((ov.timeline_end - ov.timeline_start) / totalDur) * 100;
                  return (
                    <div
                      key={ov.overlay_id}
                      title={`Caption '${ov.content}' at ${ov.timeline_start}s-${ov.timeline_end}s`}
                      style={{
                        position: 'absolute',
                        left: `${left}%`,
                        width: `${Math.max(width, 3)}%`,
                        height: '100%',
                        background: ov.color === '#FF2A2A' ? '#ef4444' : '#10b981',
                        borderRadius: '4px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '0.65rem',
                        fontWeight: '700',
                        color: '#ffffff',
                        overflow: 'hidden',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {ov.content}
                    </div>
                  );
                })}
              </div>
            )}
          </div>

          {/* Interactive Word-Level Transcript Viewer */}
          <div style={{ flex: 1, padding: '20px 24px', overflowY: 'auto' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <h3 style={{ fontSize: '0.85rem', fontWeight: '600', color: 'var(--text-muted)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
                Interactive Word-Level Transcript ({project?.transcript?.words?.length || 0} tokens)
              </h3>
              {project?.transcript && (
                <span style={{ fontSize: '0.75rem', color: '#a5b4fc', background: 'rgba(99, 102, 241, 0.15)', padding: '3px 8px', borderRadius: '4px' }}>
                  Detected: <strong>{project.transcript.language_detected}</strong> → Output: <strong>{project.transcript.transcript_language}</strong> ({project.transcript.task})
                </span>
              )}
            </div>
            <div style={{ lineHeight: '2.0', background: 'rgba(15, 23, 42, 0.3)', padding: '16px', borderRadius: '10px', border: '1px solid var(--border-subtle)' }}>
              {project?.transcript?.words?.map((w) => {
                const isFiller = ['um', 'uh', 'ah', 'erm'].includes(w.word.toLowerCase().replace(/[.,!]/g, ''));
                const isPricing = w.word.toLowerCase().includes('pricing');
                const isWelcome = ['welcome', 'channel'].includes(w.word.toLowerCase().replace(/[.,!]/g, ''));
                const isPlaying = currentTime >= w.start_s && currentTime <= w.end_s;

                return (
                  <span
                    key={w.id}
                    className={`word-pill ${isFiller ? 'filler' : ''} ${isWelcome ? 'captioned' : ''}`}
                    style={{
                      background: isPlaying ? '#4f46e5' : isPricing ? 'rgba(234, 179, 8, 0.25)' : undefined,
                      color: isPlaying ? '#ffffff' : undefined,
                      border: isPricing ? '1px solid #eab308' : undefined,
                    }}
                    title={`[${w.speaker}] ${w.start_s.toFixed(2)}s - ${w.end_s.toFixed(2)}s (Conf: ${w.confidence.toFixed(2)})`}
                    onClick={() => {
                      if (videoRef.current) videoRef.current.currentTime = w.start_s;
                    }}
                  >
                    {w.word}
                  </span>
                );
              })}
            </div>
          </div>
        </div>

        {/* Right: Conversational Prompt Bar & Response Feed */}
        <div style={{ display: 'flex', flexDirection: 'column', background: 'var(--bg-secondary)', overflow: 'hidden' }}>
          
          {/* Header */}
          <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border-subtle)' }}>
            <h2 style={{ fontSize: '0.95rem', fontWeight: '700' }}>Editing Instructions & History</h2>
            <p style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>Strict 3-Way Answers: Applied, Clarification, Rejected</p>
          </div>

          {/* Quick Suggestions Drawer */}
          <div style={{ padding: '10px 20px', background: 'rgba(10, 14, 23, 0.4)', borderBottom: '1px solid var(--border-subtle)' }}>
            <span style={{ fontSize: '0.7rem', color: 'var(--text-faint)', display: 'block', marginBottom: '6px' }}>
              Quick Worked Example Steps:
            </span>
            <div style={{ display: 'flex', gap: '6px', overflowX: 'auto', paddingBottom: '4px' }}>
              {workedSteps.map((step, idx) => (
                <button
                  key={idx}
                  className="btn-secondary"
                  style={{ fontSize: '0.7rem', padding: '4px 8px', whiteSpace: 'nowrap' }}
                  onClick={() => executeInstruction(step)}
                >
                  {idx+1}. {step.length > 25 ? step.substring(0, 25) + '...' : step}
                </button>
              ))}
            </div>
          </div>

          {/* Response Feed */}
          <div style={{ flex: 1, padding: '16px 20px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
            
            {/* Active Clarification Card (if pending) */}
            {pendingClarification && (
              <div style={{
                background: 'rgba(245, 158, 11, 0.12)',
                border: '1px solid rgba(245, 158, 11, 0.4)',
                borderRadius: '10px',
                padding: '14px',
              }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <span className="badge-clarification">Needs Clarification</span>
                </div>
                <p style={{ fontSize: '0.85rem', fontWeight: '600', color: '#fef3c7', marginBottom: '10px' }}>
                  {pendingClarification.question}
                </p>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px' }}>
                  {pendingClarification.options?.map((opt, i) => (
                    <button
                      key={i}
                      className="btn-secondary"
                      style={{ background: 'rgba(245, 158, 11, 0.25)', borderColor: '#f59e0b', color: '#fff' }}
                      onClick={() => executeInstruction(opt)}
                    >
                      {opt}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* Instruction History Cards */}
            {history.map((item, idx) => (
              <div
                key={idx}
                style={{
                  background: 'var(--bg-card)',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '10px',
                  padding: '12px 14px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                  <span style={{ fontSize: '0.8rem', fontWeight: '600', color: '#cbd5e1' }}>
                    "{item.prompt}"
                  </span>
                  <span className={
                    item.result.status === 'applied' ? 'badge-applied' :
                    item.result.status === 'needs_clarification' ? 'badge-clarification' :
                    'badge-rejected'
                  }>
                    {item.result.status}
                  </span>
                </div>
                <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                  {item.result.summary}
                </p>
                {item.result.status === 'applied' && (
                  <div style={{ marginTop: '6px', fontSize: '0.72rem', color: 'var(--text-faint)', display: 'flex', gap: '12px' }}>
                    <span>Duration: {item.result.duration_s?.toFixed(2)}s</span>
                    <span>Hash: {item.result.timeline_hash?.substring(0, 8)}...</span>
                  </div>
                )}
              </div>
            ))}

            {history.length === 0 && !pendingClarification && (
              <div style={{ textAlign: 'center', padding: '40px 10px', color: 'var(--text-faint)', fontSize: '0.85rem' }}>
                Type an instruction below or select a worked example step above.
              </div>
            )}
          </div>

          {/* Render Job Banner */}
          {renderJob && (
            <div style={{ padding: '12px 20px', background: 'rgba(99, 102, 241, 0.15)', borderTop: '1px solid rgba(99, 102, 241, 0.3)' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', fontSize: '0.8rem' }}>
                <span>Render Status: <strong>{renderJob.status}</strong></span>
                <span>{renderJob.progress}%</span>
              </div>
              <div style={{ height: '4px', background: '#1e293b', borderRadius: '2px', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: `${renderJob.progress}%`, background: '#6366f1', transition: 'width 0.3s ease' }}></div>
              </div>
              {renderJob.result_links?.video_url && (
                <div style={{ marginTop: '8px', display: 'flex', gap: '8px' }}>
                  <a href={renderJob.result_links.video_url} download className="btn-primary" style={{ fontSize: '0.75rem', padding: '4px 10px' }}>
                    Download MP4
                  </a>
                  <a href={renderJob.result_links.manifest_url} download className="btn-secondary" style={{ fontSize: '0.75rem', padding: '4px 10px' }}>
                    View Manifest
                  </a>
                </div>
              )}
            </div>
          )}

          {/* Input Prompt Bar */}
          <div style={{ padding: '16px 20px', borderTop: '1px solid var(--border-subtle)', background: '#0c111d' }}>
            <form onSubmit={(e) => { e.preventDefault(); executeInstruction(); }} style={{ display: 'flex', gap: '8px' }}>
              <input
                type="text"
                placeholder="Type an edit (e.g. 'Cut the first 30 seconds', 'Undo')..."
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
                style={{
                  flex: 1,
                  background: 'rgba(15, 23, 42, 0.8)',
                  border: '1px solid #334155',
                  borderRadius: '8px',
                  padding: '10px 14px',
                  color: '#fff',
                  fontSize: '0.85rem',
                  outline: 'none',
                }}
              />
              <button type="submit" className="btn-primary" disabled={loading || !instruction.trim()}>
                Apply
              </button>
            </form>
          </div>

        </div>
      </main>
    </div>
  );
}
