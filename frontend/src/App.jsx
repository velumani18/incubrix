import React, { useState, useEffect, useRef } from 'react';

// Format seconds into MM:SS.s
function formatTime(secs) {
  if (isNaN(secs) || secs === null || secs === undefined) return '00:00.0';
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  const ms = Math.floor((secs % 1) * 10);
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}.${ms}`;
}

// Convert raw source media time -> virtual edited timeline time
function sourceToTimelineTime(srcTime, segments) {
  if (!segments || segments.length === 0) return srcTime;
  let accumulatedTimeline = 0;
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const segDur = seg.timeline_duration || ((seg.source_end - seg.source_start) / (seg.speed || 1.0));
    if (srcTime < seg.source_start) {
      return accumulatedTimeline;
    }
    if (srcTime <= seg.source_end) {
      const offset = (srcTime - seg.source_start) / (seg.speed || 1.0);
      return Math.min(accumulatedTimeline + offset, accumulatedTimeline + segDur);
    }
    accumulatedTimeline += segDur;
  }
  return accumulatedTimeline;
}

// Convert virtual edited timeline time -> raw source media time
function timelineToSourceTime(timelineTime, segments) {
  if (!segments || segments.length === 0) return timelineTime;
  let accumulated = 0;
  for (let i = 0; i < segments.length; i++) {
    const seg = segments[i];
    const segDur = seg.timeline_duration || ((seg.source_end - seg.source_start) / (seg.speed || 1.0));
    if (timelineTime <= accumulated + segDur) {
      const segOffset = (timelineTime - accumulated) * (seg.speed || 1.0);
      return Math.min(seg.source_end, seg.source_start + segOffset);
    }
    accumulated += segDur;
  }
  return segments[segments.length - 1].source_end;
}

// Determine whether a source timestamp falls within any active timeline segment
function isSourceTimeInTimeline(srcTime, segments) {
  if (!segments || segments.length === 0) return true;
  return segments.some(s => srcTime >= s.source_start - 0.04 && srcTime <= s.source_end + 0.04);
}

export default function App() {
  const [projectList, setProjectList] = useState([]);
  const [project, setProject] = useState(null);
  const [loading, setLoading] = useState(false);
  const [globalError, setGlobalError] = useState(null);
  const [currentTime, setCurrentTime] = useState(0); // Timeline virtual time (0 to duration_s)
  const [sourceTime, setSourceTime] = useState(0);   // Raw source media timestamp
  const [duration, setDuration] = useState(0);
  const [isPlaying, setIsPlaying] = useState(false);
  const [volume, setVolume] = useState(1);
  const [isMuted, setIsMuted] = useState(false);
  const [playbackRate, setPlaybackRate] = useState(1);

  // Transcript view options
  const [transcriptViewMode, setTranscriptViewMode] = useState('segments'); // 'segments' | 'flow'
  const [autoScroll, setAutoScroll] = useState(true);

  // Range selection for deterministic edits
  const [selectedWordStart, setSelectedWordStart] = useState(null); // Word object
  const [selectedWordEnd, setSelectedWordEnd] = useState(null);     // Word object
  const [manualStart, setManualStart] = useState('');
  const [manualEnd, setManualEnd] = useState('');

  // Add caption modal state
  const [isCaptionModalOpen, setIsCaptionModalOpen] = useState(false);
  const [captionText, setCaptionText] = useState('');
  const [captionColor, setCaptionColor] = useState('#FFFFFF');
  const [captionPosition, setCaptionPosition] = useState('bottom');

  // Edit preview / history feedback
  const [lastEditFeedback, setLastEditFeedback] = useState(null);

  // Version history & restoration
  const [versionList, setVersionList] = useState([]);
  const [isVersionModalOpen, setIsVersionModalOpen] = useState(false);

  // Upload & Transcription progress
  const [uploadProgress, setUploadProgress] = useState(null);
  const [sttTask, setSttTask] = useState('transcribe');

  // Natural-Language AI Assistant state
  const [instruction, setInstruction] = useState('');
  const [activeProposal, setActiveProposal] = useState(null);
  const [assistantLoading, setAssistantLoading] = useState(false);
  const [history, setHistory] = useState([]);
  const [pendingClarification, setPendingClarification] = useState(null);

  // Rendering state
  const [renderJob, setRenderJob] = useState(null);

  const videoRef = useRef(null);
  const activeWordRef = useRef(null);
  const timelineRulerRef = useRef(null);

  // Load project list on mount
  useEffect(() => {
    fetchProjects();
  }, []);

  const fetchProjects = async () => {
    try {
      const res = await fetch('/api/v1/projects');
      if (res.ok) {
        const projs = await res.json();
        setProjectList(projs || []);
        if (projs && projs.length > 0 && !project) {
          loadProject(projs[0].project_id);
        }
      }
    } catch (e) {
      console.error('Failed to fetch projects', e);
    }
  };

  const loadProject = async (id) => {
    setLoading(true);
    setGlobalError(null);
    try {
      const res = await fetch(`/api/v1/projects/${id}`);
      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.error?.message || `Failed to load project (${res.status})`);
      }
      const data = await res.json();
      setProject(data);
      if (data.pending_clarification) {
        setPendingClarification(data.pending_clarification);
      }
      // Instantly conform player position to the updated/restored timeline
      const segments = data.timeline?.segments || [];
      if (segments.length > 0) {
        const firstStart = segments[0].source_start;
        if (videoRef.current) {
          videoRef.current.currentTime = firstStart;
        }
        setSourceTime(firstStart);
        setCurrentTime(0);
      }
    } catch (err) {
      setGlobalError(err.message || 'Error loading project.');
    } finally {
      setLoading(false);
    }
  };

  // Video playback listeners
  const onTimeUpdate = (e) => {
    const cur = e.target.currentTime;
    setSourceTime(cur);
    const segments = project?.timeline?.segments;
    if (segments && segments.length > 0) {
      const vTime = sourceToTimelineTime(cur, segments);
      setCurrentTime(vTime);
    } else {
      setCurrentTime(cur);
    }
    if (e.target.duration && !isNaN(e.target.duration)) {
      setDuration(e.target.duration);
    }
  };

  const togglePlay = () => {
    if (!videoRef.current) return;
    if (videoRef.current.paused) {
      // If we are at the end, restart from first segment
      const segments = project?.timeline?.segments || [];
      if (segments.length > 0) {
        const lastSeg = segments[segments.length - 1];
        if (videoRef.current.currentTime >= lastSeg.source_end - 0.05) {
          videoRef.current.currentTime = segments[0].source_start;
        }
      }
      videoRef.current.play().then(() => setIsPlaying(true)).catch(console.error);
    } else {
      videoRef.current.pause();
      setIsPlaying(false);
    }
  };

  // Dynamic frame-by-frame timeline playback conformance (skips cut sections dynamically)
  useEffect(() => {
    let animId;
    const segments = project?.timeline?.segments;
    if (!segments || segments.length === 0) return;

    const checkFrame = () => {
      const vid = videoRef.current;
      if (vid && !vid.paused && !vid.seeking) {
        const cur = vid.currentTime;
        setSourceTime(cur);

        // Find which segment currently contains cur
        let activeIdx = -1;
        for (let i = 0; i < segments.length; i++) {
          const s = segments[i];
          if (cur >= s.source_start - 0.02 && cur < s.source_end) {
            activeIdx = i;
            break;
          }
        }

        if (activeIdx !== -1) {
          const activeSeg = segments[activeIdx];
          // Check if video reached the boundary of this active segment
          if (cur >= activeSeg.source_end - 0.04) {
            if (activeIdx < segments.length - 1) {
              // Seamlessly jump cut over the deleted section to the next segment!
              const nextStart = segments[activeIdx + 1].source_start;
              vid.currentTime = nextStart;
              setSourceTime(nextStart);
              setCurrentTime(sourceToTimelineTime(nextStart, segments));
            } else {
              // Reached end of final segment
              vid.pause();
              setIsPlaying(false);
              const firstStart = segments[0].source_start;
              vid.currentTime = firstStart;
              setSourceTime(firstStart);
              setCurrentTime(0);
              return;
            }
          } else {
            // Apply dynamic audio mute for this segment
            vid.muted = isMuted || Boolean(activeSeg.is_muted);
            // Apply dynamic playback speed for this segment
            const targetRate = playbackRate * (activeSeg.speed || 1.0);
            if (Math.abs(vid.playbackRate - targetRate) > 0.01) {
              vid.playbackRate = targetRate;
            }
            setCurrentTime(sourceToTimelineTime(cur, segments));
          }
        } else {
          // Playhead is in a cut gap between segments!
          const nextSeg = segments.find(s => s.source_start > cur);
          if (nextSeg) {
            vid.currentTime = nextSeg.source_start;
            setSourceTime(nextSeg.source_start);
            setCurrentTime(sourceToTimelineTime(nextSeg.source_start, segments));
          } else if (cur < segments[0].source_start) {
            vid.currentTime = segments[0].source_start;
            setSourceTime(segments[0].source_start);
            setCurrentTime(0);
          } else {
            // Past all segments
            vid.pause();
            setIsPlaying(false);
            const firstStart = segments[0].source_start;
            vid.currentTime = firstStart;
            setSourceTime(firstStart);
            setCurrentTime(0);
            return;
          }
        }
      }

      if (isPlaying) {
        animId = requestAnimationFrame(checkFrame);
      }
    };

    if (isPlaying) {
      animId = requestAnimationFrame(checkFrame);
    }
    return () => {
      if (animId) cancelAnimationFrame(animId);
    };
  }, [isPlaying, project, isMuted, playbackRate]);

  // Virtual timeline seek (used by ruler and scrubber)
  const handleSeekVirtual = (timelineSecs) => {
    const segments = project?.timeline?.segments || [];
    const totalDur = project?.timeline?.duration_s || duration || 1;
    const clampedVTime = Math.max(0, Math.min(timelineSecs, totalDur));
    const srcTime = timelineToSourceTime(clampedVTime, segments);
    if (videoRef.current) {
      videoRef.current.currentTime = srcTime;
    }
    setCurrentTime(clampedVTime);
    setSourceTime(srcTime);
  };

  // Source media seek (used by clicking words, segment cards, or EDL slice pills)
  const handleSeekSource = (sourceSecs) => {
    const segments = project?.timeline?.segments || [];
    if (videoRef.current) {
      videoRef.current.currentTime = sourceSecs;
    }
    setSourceTime(sourceSecs);
    const vTime = sourceToTimelineTime(sourceSecs, segments);
    setCurrentTime(vTime);
  };

  // Backwards compatibility alias
  const handleSeek = (timeSecs) => {
    handleSeekSource(timeSecs);
  };

  // Timeline ruler click seeking
  const handleRulerClick = (e) => {
    if (!timelineRulerRef.current) return;
    const rect = timelineRulerRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const ratio = Math.max(0, Math.min(1, clickX / rect.width));
    const totalDur = project?.timeline?.duration_s || duration || 1;
    handleSeekVirtual(ratio * totalDur);
  };

  // Keyboard shortcut: Spacebar to toggle play/pause
  useEffect(() => {
    const handleKeyDown = (e) => {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.code === 'Space') {
        e.preventDefault();
        togglePlay();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isPlaying]);

  // Handle Video Upload
  const handleFileUpload = async (e) => {
    const file = e.target.files[0];
    if (!file) return;

    // Validate size (500MB max) & extension (.mp4, .mov, .mkv)
    const ext = file.name.substring(file.name.lastIndexOf('.')).toLowerCase();
    if (!['.mp4', '.mov', '.mkv'].includes(ext)) {
      setGlobalError(`Unsupported format '${ext}'. Permitted formats: MP4, MOV, MKV.`);
      return;
    }
    if (file.size > 500 * 1024 * 1024) {
      setGlobalError(`File size (${(file.size / (1024 * 1024)).toFixed(1)}MB) exceeds the 500MB limit.`);
      return;
    }

    setLoading(true);
    setGlobalError(null);
    setUploadProgress({ percent: 15, message: 'Uploading video file...' });

    const formData = new FormData();
    formData.append('file', file);
    formData.append('task', sttTask);

    try {
      setUploadProgress({ percent: 45, message: `Extracting audio & running Whisper STT (${sttTask})...` });
      const res = await fetch('/api/v1/projects/upload', {
        method: 'POST',
        headers: { 'Idempotency-Key': `upload_${Date.now()}` },
        body: formData,
      });
      const data = await res.json();
      if (res.ok) {
        setUploadProgress({ percent: 100, message: 'Transcription completed successfully!' });
        await fetchProjects();
        await loadProject(data.project_id);
      } else {
        throw new Error(data.error?.message || 'Upload & transcription failed.');
      }
    } catch (err) {
      setGlobalError(`Transcription error: ${err.message}`);
    } finally {
      setLoading(false);
      setTimeout(() => setUploadProgress(null), 1800);
    }
  };

  // Word selection logic
  const handleWordClick = (word, e) => {
    if (e.shiftKey && selectedWordStart) {
      // Range selection
      const allWords = project?.transcript?.words || [];
      const idxStart = allWords.findIndex(w => w.id === selectedWordStart.id);
      const idxEnd = allWords.findIndex(w => w.id === word.id);
      if (idxStart !== -1 && idxEnd !== -1) {
        const [first, second] = idxStart <= idxEnd ? [selectedWordStart, word] : [word, selectedWordStart];
        setSelectedWordStart(first);
        setSelectedWordEnd(second);
        setManualStart(first.start_s.toFixed(2));
        setManualEnd(second.end_s.toFixed(2));
      }
    } else {
      // Seek video to word start time
      handleSeek(word.start_s);
      // Select single word as starting point
      setSelectedWordStart(word);
      setSelectedWordEnd(word);
      setManualStart(word.start_s.toFixed(2));
      setManualEnd(word.end_s.toFixed(2));
    }
  };

  // Segment selection logic
  const handleSelectSegment = (segment) => {
    handleSeek(segment.start);
    const startW = segment.words?.[0] || { id: segment.id, start_s: segment.start, end_s: segment.start };
    const endW = segment.words?.[segment.words.length - 1] || { id: segment.id, start_s: segment.end, end_s: segment.end };
    setSelectedWordStart(startW);
    setSelectedWordEnd(endW);
    setManualStart(segment.start.toFixed(2));
    setManualEnd(segment.end.toFixed(2));
  };

  const clearSelection = () => {
    setSelectedWordStart(null);
    setSelectedWordEnd(null);
    setManualStart('');
    setManualEnd('');
  };

  // Deterministic Edit Execution
  const executeDeterministicEdit = async (operationType, additionalParams = {}) => {
    if (!project) return;
    const sStart = parseFloat(manualStart);
    const sEnd = parseFloat(manualEnd);

    if (isNaN(sStart) || isNaN(sEnd) || sStart >= sEnd) {
      setGlobalError('Please select a valid range where Start Time is strictly less than End Time.');
      return;
    }

    setLoading(true);
    setGlobalError(null);

    const wordIds = [];
    if (selectedWordStart && selectedWordEnd) {
      wordIds.push(selectedWordStart.id);
      if (selectedWordEnd.id !== selectedWordStart.id) {
        wordIds.push(selectedWordEnd.id);
      }
    }

    const payload = {
      operations: [
        {
          operation: operationType,
          start_time: sStart,
          end_time: sEnd,
          time_policy: project.timeline.time_policy,
          word_ids: wordIds,
          ...additionalParams
        }
      ]
    };

    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/edits`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `edit_${Date.now()}`
        },
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.error?.message || `Edit rejected (${res.status})`);
      }

      setLastEditFeedback({
        operation: operationType,
        before: data.before_duration_s,
        after: data.after_duration_s,
        delta: data.duration_delta_s,
        hash: data.timeline_hash
      });

      clearSelection();
      await loadProject(project.project_id);
    } catch (err) {
      setGlobalError(err.message || 'Edit operation failed.');
    } finally {
      setLoading(false);
    }
  };

  // Undo / Redo
  const handleUndo = async () => {
    if (!project || !project.can_undo) return;
    setLoading(true);
    setGlobalError(null);
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/edits/undo`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || 'Undo failed');
      setLastEditFeedback({
        operation: 'undo',
        before: project.timeline.duration_s,
        after: data.duration_s,
        delta: data.duration_s - project.timeline.duration_s,
        hash: data.timeline_hash
      });
      clearSelection();
      await loadProject(project.project_id);
    } catch (e) {
      setGlobalError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const handleRedo = async () => {
    if (!project || !project.can_redo) return;
    setLoading(true);
    setGlobalError(null);
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/edits/redo`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || 'Redo failed');
      setLastEditFeedback({
        operation: 'redo',
        before: project.timeline.duration_s,
        after: data.duration_s,
        delta: data.duration_s - project.timeline.duration_s,
        hash: data.timeline_hash
      });
      clearSelection();
      await loadProject(project.project_id);
    } catch (e) {
      setGlobalError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // Version History Inspection & Restoration
  const openVersionModal = async () => {
    if (!project) return;
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/versions`);
      if (res.ok) {
        const data = await res.json();
        setVersionList(data.versions || []);
        setIsVersionModalOpen(true);
      }
    } catch (e) {
      setGlobalError('Failed to fetch version history');
    }
  };

  const handleRestoreVersion = async (vIndex) => {
    if (!project) return;
    setLoading(true);
    setGlobalError(null);
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/restore/${vIndex}`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error?.message || 'Version restoration failed');
      setIsVersionModalOpen(false);
      setLastEditFeedback({
        operation: `restoration to version ${vIndex}`,
        before: project.timeline.duration_s,
        after: data.duration_s,
        delta: data.duration_s - project.timeline.duration_s,
        hash: data.timeline_hash
      });
      clearSelection();
      await loadProject(project.project_id);
    } catch (e) {
      setGlobalError(e.message);
    } finally {
      setLoading(false);
    }
  };

  // Natural Language AI Editing Assistant: Propose, Preview & Apply Flow
  const proposeAssistantEdit = async (textToSend) => {
    const promptText = (textToSend || instruction || '').trim();
    if (!promptText || !project) return;

    // Shortcuts for quick undo/redo
    if (promptText.toLowerCase() === 'undo') {
      await handleUndo();
      setInstruction('');
      return;
    }
    if (promptText.toLowerCase() === 'redo') {
      await handleRedo();
      setInstruction('');
      return;
    }

    setAssistantLoading(true);
    setGlobalError(null);
    setPendingClarification(null);
    try {
      const res = await fetch(`/api/v1/projects/${project.project_id}/assistant/propose`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `asst_prop_${Date.now()}`
        },
        body: JSON.stringify({ instruction: promptText }),
      });
      const result = await res.json();
      if (!res.ok) {
        throw new Error(result.error?.message || result.detail || 'Assistant proposal request failed');
      }

      setInstruction('');

      if (result.status === 'proposed') {
        setActiveProposal(result);
        setPendingClarification(null);
      } else if (result.status === 'needs_clarification') {
        setActiveProposal(null);
        setPendingClarification({
          question: result.question || result.clarification?.question || 'Please clarify your edit request:',
          options: result.options || result.clarification?.options || [],
          instruction: promptText,
        });
      } else if (result.status === 'rejected') {
        setActiveProposal(null);
        setPendingClarification(null);
        setHistory(prev => [{
          prompt: promptText,
          result: {
            status: 'rejected',
            summary: result.rejection_reason || 'Request rejected based on transcript and timeline limits.',
            explanation: result.explanation
          }
        }, ...prev]);
      } else {
        // error status
        setActiveProposal(null);
        setPendingClarification(null);
        setHistory(prev => [{
          prompt: promptText,
          result: {
            status: 'error',
            summary: result.rejection_reason || result.explanation || 'Processing error occurred.',
          }
        }, ...prev]);
      }
    } catch (err) {
      setGlobalError(err.message || 'Assistant request failed');
    } finally {
      setAssistantLoading(false);
    }
  };

  const applyAssistantProposal = async () => {
    if (!activeProposal || !project) return;

    setLoading(true);
    setGlobalError(null);
    try {
      const promptTitle = activeProposal.instruction || activeProposal.user_instruction || 'AI Edit';
      const res = await fetch(`/api/v1/projects/${project.project_id}/assistant/apply`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Idempotency-Key': `asst_apply_${Date.now()}`
        },
        body: JSON.stringify({
          proposal_id: activeProposal.proposal_id,
          instruction: promptTitle
        }),
      });
      const result = await res.json();
      if (!res.ok) {
        throw new Error(result.error?.message || result.detail || 'Failed to apply proposed edit');
      }

      // Add applied operation to conversational history
      setHistory(prev => [{
        prompt: promptTitle,
        result: {
          status: 'applied',
          summary: result.summary,
          duration_s: result.duration_s,
          timeline_hash: result.timeline_hash,
          version: result.version
        }
      }, ...prev]);

      setLastEditFeedback({
        operation: promptTitle,
        before: activeProposal.preview?.before_duration_s ?? project.timeline.duration_s,
        after: result.duration_s,
        delta: activeProposal.preview?.duration_delta_s ?? 0,
        hash: result.timeline_hash
      });

      setActiveProposal(null);
      await loadProject(project.project_id);
    } catch (err) {
      setGlobalError(err.message || 'Failed to apply proposed edit');
    } finally {
      setLoading(false);
    }
  };

  const discardAssistantProposal = () => {
    setActiveProposal(null);
  };

  // Render pipeline trigger
  const triggerRender = async () => {
    if (!project) return;
    setLoading(true);
    setGlobalError(null);
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

      // Poll background job
      const pollInterval = setInterval(async () => {
        try {
          const pollRes = await fetch(`/api/v1/jobs/${data.job_id}`);
          const jobData = await pollRes.json();
          setRenderJob(jobData);
          if (jobData.status === 'succeeded' || jobData.status === 'failed') {
            clearInterval(pollInterval);
            if (jobData.status === 'failed') {
              setGlobalError(`Rendering failed: ${jobData.error?.message || 'FFmpeg render error'}`);
            }
          }
        } catch (e) {
          clearInterval(pollInterval);
        }
      }, 1200);

    } catch (err) {
      setGlobalError(`Render request failed: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  // Active overlay burn-in simulation
  const activeOverlay = project?.projected_overlays?.find(
    ov => currentTime >= ov.timeline_start && currentTime <= ov.timeline_end
  );

  // Auto scroll to active word
  useEffect(() => {
    if (autoScroll && activeWordRef.current) {
      activeWordRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }, [sourceTime, autoScroll]);

  const totalDuration = project?.timeline?.duration_s || duration || 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-primary)' }}>
      {/* Top Navbar */}
      <header style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '10px 20px',
        borderBottom: '1px solid var(--border-subtle)',
        background: 'rgba(13, 19, 34, 0.95)',
        backdropFilter: 'blur(10px)',
        zIndex: 50
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{
            background: 'linear-gradient(135deg, #6366f1, #38bdf8)',
            width: '32px',
            height: '32px',
            borderRadius: '8px',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontWeight: '800',
            fontSize: '17px',
            color: '#fff',
            boxShadow: '0 0 10px rgba(99, 102, 241, 0.5)'
          }}>
            I
          </div>
          <div>
            <h1 style={{ fontSize: '1.05rem', fontWeight: '700', letterSpacing: '-0.02em', color: '#fff' }}>
              IncuBrix Studio
            </h1>
            <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)' }}>
              Deterministic Text-Based & Prompt Video Editor
            </p>
          </div>
        </div>

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          {project && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', fontSize: '0.78rem' }}>
              <span style={{ color: 'var(--text-muted)' }}>
                Policy: <strong style={{ color: '#818cf8' }}>{project.timeline.time_policy}</strong>
              </span>
              <span style={{ color: 'var(--text-muted)' }}>
                Duration: <strong style={{ color: '#38bdf8' }}>{project.timeline.duration_s.toFixed(2)}s</strong>
              </span>
              <span style={{
                background: 'rgba(99, 102, 241, 0.1)',
                padding: '3px 7px',
                borderRadius: '5px',
                fontFamily: 'var(--font-mono)',
                fontSize: '0.72rem',
                color: '#a5b4fc',
              }}>
                Hash: {project.timeline.timeline_hash.substring(0, 8)}
              </span>
              <button
                className={project.can_undo ? "btn-nav-action" : "btn-secondary"}
                onClick={handleUndo}
                disabled={!project.can_undo || loading}
                title="Undo last edit step"
                style={{ padding: '6px 12px', fontSize: '0.8rem' }}
              >
                ↺ Undo
              </button>
              <button
                className={project.can_redo ? "btn-nav-action" : "btn-secondary"}
                onClick={handleRedo}
                disabled={!project.can_redo || loading}
                title="Redo last edit step"
                style={{ padding: '6px 12px', fontSize: '0.8rem' }}
              >
                ↻ Redo
              </button>
              <button
                className="btn-secondary"
                onClick={openVersionModal}
                title="View full version history and restore saved states"
                style={{ padding: '6px 12px', fontSize: '0.8rem', borderColor: '#818cf8', color: '#c7d2fe', fontWeight: '700' }}
              >
                📜 Versions (v{project.current_version ?? 0})
              </button>
            </div>
          )}

          {projectList.length > 0 && (
            <select
              value={project?.project_id || ''}
              onChange={(e) => loadProject(e.target.value)}
              style={{
                background: '#1e293b',
                color: '#fff',
                border: '1.5px solid #475569',
                borderRadius: '8px',
                padding: '7px 12px',
                fontSize: '0.8rem',
                fontWeight: '600',
                outline: 'none',
                cursor: 'pointer'
              }}
            >
              {projectList.map(p => (
                <option key={p.project_id} value={p.project_id}>
                  {p.project_id} ({p.duration_s?.toFixed(1) || '?'}s)
                </option>
              ))}
            </select>
          )}

          <label className="btn-secondary" style={{ cursor: 'pointer', padding: '7px 14px', fontSize: '0.82rem', borderColor: '#38bdf8', color: '#38bdf8', fontWeight: '700' }}>
            <span>+ Upload Video</span>
            <input type="file" accept=".mp4,.mov,.mkv" onChange={handleFileUpload} style={{ display: 'none' }} />
          </label>

          <button className="btn-primary" onClick={triggerRender} disabled={!project || loading} style={{ padding: '7px 16px' }}>
            ⚡ Render Final MP4
          </button>
        </div>
      </header>

      {/* Global Error Banner */}
      {globalError && (
        <div style={{
          background: 'rgba(239, 68, 68, 0.15)',
          borderBottom: '1px solid rgba(239, 68, 68, 0.4)',
          color: '#fca5a5',
          padding: '8px 20px',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          fontSize: '0.82rem',
          zIndex: 40
        }}>
          <span><strong>Error:</strong> {globalError}</span>
          <div style={{ display: 'flex', gap: '8px' }}>
            {project && (
              <button className="btn-danger" onClick={() => loadProject(project.project_id)} style={{ padding: '2px 8px', fontSize: '0.75rem' }}>
                Retry
              </button>
            )}
            <button onClick={() => setGlobalError(null)} style={{ background: 'none', border: 'none', color: '#fca5a5', cursor: 'pointer', fontWeight: 'bold' }}>
              ✕
            </button>
          </div>
        </div>
      )}

      {/* Upload & Transcription Progress Banner */}
      {uploadProgress && (
        <div style={{
          background: 'rgba(99, 102, 241, 0.15)',
          borderBottom: '1px solid rgba(99, 102, 241, 0.3)',
          padding: '8px 20px',
          fontSize: '0.8rem',
          color: '#e0e7ff',
        }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
            <span>{uploadProgress.message}</span>
            <span>{uploadProgress.percent}%</span>
          </div>
          <div style={{ height: '4px', background: '#1e293b', borderRadius: '2px', overflow: 'hidden' }}>
            <div style={{ height: '100%', width: `${uploadProgress.percent}%`, background: '#6366f1', transition: 'width 0.3s ease' }}></div>
          </div>
        </div>
      )}

      {/* Empty State: No Project */}
      {!project && !loading && (
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '40px' }}>
          <div className="glass-panel" style={{ maxWidth: '520px', width: '100%', padding: '36px', textAlign: 'center' }}>
            <div style={{
              width: '54px',
              height: '54px',
              borderRadius: '50%',
              background: 'rgba(99, 102, 241, 0.15)',
              color: '#818cf8',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              margin: '0 auto 16px',
              fontSize: '24px'
            }}>
              🎬
            </div>
            <h2 style={{ fontSize: '1.25rem', fontWeight: '700', marginBottom: '8px' }}>Start Video Editing</h2>
            <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginBottom: '24px', lineHeight: '1.5' }}>
              Upload an MP4, MOV, or MKV video up to 500MB. Our backend extracts audio with FFmpeg and produces precise word-level timestamps.
            </p>

            <div style={{ marginBottom: '20px', display: 'flex', justifyContent: 'center', gap: '8px' }}>
              <select
                value={sttTask}
                onChange={(e) => setSttTask(e.target.value)}
                style={{
                  background: 'rgba(30, 41, 59, 0.8)',
                  color: '#fff',
                  border: '1px solid var(--border-subtle)',
                  borderRadius: '8px',
                  padding: '8px 12px',
                  fontSize: '0.82rem',
                }}
              >
                <option value="transcribe">Task: Transcribe Speech</option>
                <option value="translate">Task: Translate Speech to English</option>
              </select>
            </div>

            <label className="dropzone" style={{ display: 'block', marginBottom: '16px' }}>
              <div style={{ fontSize: '0.9rem', fontWeight: '600', color: '#e2e8f0', marginBottom: '4px' }}>
                Click to browse or drag & drop video
              </div>
              <div style={{ fontSize: '0.75rem', color: 'var(--text-faint)' }}>
                Supported: .mp4, .mov, .mkv (Max 500MB)
              </div>
              <input type="file" accept=".mp4,.mov,.mkv" onChange={handleFileUpload} style={{ display: 'none' }} />
            </label>

            {projectList.length > 0 && (
              <div style={{ marginTop: '16px', borderTop: '1px solid var(--border-subtle)', paddingTop: '16px' }}>
                <span style={{ fontSize: '0.75rem', color: 'var(--text-faint)', display: 'block', marginBottom: '8px' }}>
                  Or select an existing project:
                </span>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', justifyContent: 'center' }}>
                  {projectList.slice(0, 4).map(p => (
                    <button
                      key={p.project_id}
                      className="btn-secondary"
                      style={{ fontSize: '0.75rem', padding: '4px 8px' }}
                      onClick={() => loadProject(p.project_id)}
                    >
                      {p.project_id} ({p.duration_s?.toFixed(1) || '?'}s)
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Main Video Editing Workspace */}
      {project && (
        <main style={{ display: 'grid', gridTemplateColumns: '1fr 400px', flex: 1, overflow: 'hidden' }}>
          
          {/* Left Column: Player, Timeline Ruler, Interactive Transcript & Edit Actions */}
          <div style={{ display: 'flex', flexDirection: 'column', borderRight: '1px solid var(--border-subtle)', overflowY: 'auto' }}>
            
            {/* Video Player Card */}
            <div style={{ padding: '12px 20px', background: '#05070c', display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
              <div style={{
                position: 'relative',
                width: '100%',
                maxWidth: '560px',
                maxHeight: '34vh',
                aspectRatio: '16/9',
                background: '#000',
                borderRadius: '10px',
                overflow: 'hidden',
                boxShadow: '0 8px 24px rgba(0,0,0,0.6)',
                border: '1.5px solid #334155'
              }}>
                <video
                  ref={videoRef}
                  src={`/api/v1/media/${project.source_media.filename}`}
                  onTimeUpdate={onTimeUpdate}
                  onPlay={() => setIsPlaying(true)}
                  onPause={() => setIsPlaying(false)}
                  style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                />

                {/* Burn-in Caption Overlay Preview */}
                {activeOverlay && (
                  <div style={{
                    position: 'absolute',
                    bottom: activeOverlay.position === 'bottom' ? '24px' : 'auto',
                    top: activeOverlay.position === 'top' ? '24px' : 'auto',
                    left: '50%',
                    transform: 'translateX(-50%)',
                    background: 'rgba(0, 0, 0, 0.78)',
                    backdropFilter: 'blur(4px)',
                    padding: '6px 16px',
                    borderRadius: '6px',
                    color: activeOverlay.color || '#FFFFFF',
                    fontSize: `${activeOverlay.font_size ? activeOverlay.font_size * 0.65 : 20}px`,
                    fontWeight: '700',
                    textAlign: 'center',
                    textShadow: '0 2px 4px rgba(0,0,0,0.8)',
                    pointerEvents: 'none',
                    animation: 'fadeIn 0.15s ease-in',
                    maxWidth: '85%'
                  }}>
                    {activeOverlay.content}
                  </div>
                )}
              </div>

              {/* Synchronized Custom Player Controls */}
              <div style={{
                width: '100%',
                maxWidth: '720px',
                marginTop: '10px',
                display: 'flex',
                alignItems: 'center',
                gap: '12px',
                background: 'rgba(15, 23, 42, 0.85)',
                padding: '8px 14px',
                borderRadius: '8px',
                border: '1px solid var(--border-subtle)',
                fontSize: '0.8rem'
              }}>
                <button
                  onClick={togglePlay}
                  className="btn-primary"
                  style={{ width: '32px', height: '32px', padding: 0, borderRadius: '50%' }}
                  title="Play / Pause (Space)"
                >
                  {isPlaying ? '⏸' : '▶'}
                </button>

                <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: '8px' }}>
                  <input
                    type="range"
                    min={0}
                    max={totalDuration || 1}
                    step={0.05}
                    value={currentTime}
                    onChange={(e) => handleSeekVirtual(parseFloat(e.target.value))}
                    className="video-scrubber"
                  />
                </div>

                <div style={{ fontFamily: 'var(--font-mono)', fontSize: '0.78rem', color: '#cbd5e1', whiteSpace: 'nowrap' }}>
                  <span style={{ color: '#38bdf8' }}>{formatTime(currentTime)}</span> / {formatTime(totalDuration)}
                </div>

                {/* Volume & Rate */}
                <button
                  onClick={() => {
                    if (videoRef.current) {
                      videoRef.current.muted = !isMuted;
                      setIsMuted(!isMuted);
                    }
                  }}
                  className="btn-secondary"
                  style={{ padding: '4px 8px', fontSize: '0.75rem' }}
                  title={isMuted ? 'Unmute' : 'Mute'}
                >
                  {isMuted ? '🔇' : '🔊'}
                </button>

                <select
                  value={playbackRate}
                  onChange={(e) => {
                    const r = parseFloat(e.target.value);
                    setPlaybackRate(r);
                    if (videoRef.current) videoRef.current.playbackRate = r;
                  }}
                  style={{
                    background: 'rgba(30, 41, 59, 0.8)',
                    color: '#fff',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '4px',
                    padding: '2px 4px',
                    fontSize: '0.75rem'
                  }}
                >
                  <option value={0.5}>0.5x</option>
                  <option value={1.0}>1.0x</option>
                  <option value={1.5}>1.5x</option>
                  <option value={2.0}>2.0x</option>
                </select>
              </div>
            </div>

            {/* Timeline Ruler & Segment Visualization Track */}
            <div style={{
              padding: '14px 20px',
              borderTop: '1px solid var(--border-subtle)',
              borderBottom: '1px solid var(--border-subtle)',
              background: 'rgba(10, 15, 26, 0.6)'
            }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                <span style={{ fontSize: '0.75rem', fontWeight: '700', textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.05em' }}>
                  Timeline Track Composition (Canonical EDL: {project.timeline.segments?.length || 0} slices)
                </span>
                <span style={{ fontSize: '0.72rem', color: 'var(--text-faint)' }}>
                  Click ruler or segment to seek
                </span>
              </div>

              {/* Timeline Ruler */}
              <div
                ref={timelineRulerRef}
                onClick={handleRulerClick}
                className="timeline-ruler"
                style={{ cursor: 'pointer', marginBottom: '8px' }}
              >
                {/* Generate ruler ticks */}
                {Array.from({ length: 9 }).map((_, i) => {
                  const pct = (i / 8) * 100;
                  const sec = (i / 8) * (totalDuration || 1);
                  return (
                    <div key={i} className="timeline-tick" style={{ left: `${pct}%` }}>
                      {formatTime(sec)}
                    </div>
                  );
                })}

                {/* Scrubber Playhead */}
                {totalDuration > 0 && (
                  <div
                    className="timeline-playhead"
                    style={{ left: `${Math.min(100, (currentTime / totalDuration) * 100)}%` }}
                  />
                )}
              </div>

              {/* Segment Slices Bar */}
              <div style={{
                height: '34px',
                background: '#070a10',
                borderRadius: '6px',
                border: '1px solid #1e293b',
                display: 'flex',
                overflow: 'hidden',
                padding: '2px',
                gap: '3px',
                position: 'relative'
              }}>
                {project.timeline.segments?.map((seg, i) => {
                  const segDur = seg.timeline_duration || ((seg.source_end - seg.source_start) / (seg.speed || 1.0));
                  const widthPercent = (segDur / (totalDuration || 1)) * 100;
                  const isMutedSeg = seg.is_muted;
                  const isSpedSeg = seg.speed > 1.0;
                  const isCurrentSeg = sourceTime >= seg.source_start - 0.02 && sourceTime < seg.source_end;

                  return (
                    <div
                      key={seg.segment_id || `seg_${i}`}
                      onClick={() => handleSeekSource(seg.source_start)}
                      title={`Segment ${i+1}: [${seg.source_start.toFixed(2)}s - ${seg.source_end.toFixed(2)}s] Duration: ${segDur.toFixed(2)}s, Speed: ${seg.speed}x ${isMutedSeg ? '(MUTED)' : ''}`}
                      style={{
                        width: `${widthPercent}%`,
                        minWidth: '28px',
                        height: '100%',
                        background: isMutedSeg
                          ? 'linear-gradient(135deg, #d97706, #b45309)'
                          : isSpedSeg
                          ? 'linear-gradient(135deg, #818cf8, #6366f1)'
                          : isCurrentSeg
                          ? 'linear-gradient(135deg, #38bdf8, #2563eb)'
                          : 'linear-gradient(135deg, #2563eb, #1d4ed8)',
                        borderRadius: '4px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'center',
                        fontSize: '0.68rem',
                        fontWeight: '700',
                        color: '#fff',
                        cursor: 'pointer',
                        whiteSpace: 'nowrap',
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        padding: '0 6px',
                        userSelect: 'none',
                        boxShadow: isCurrentSeg ? '0 0 10px rgba(56, 189, 248, 0.9), inset 0 0 0 1.5px #fff' : 'none',
                        transform: isCurrentSeg ? 'scale(1.02)' : 'none',
                        transition: 'all 0.15s ease'
                      }}
                    >
                      {isMutedSeg ? `🔇 S${i+1}` : isSpedSeg ? `${seg.speed}x S${i+1}` : `S${i+1}`}
                      <span style={{ fontSize: '0.62rem', opacity: 0.85, marginLeft: '4px' }}>
                        ({segDur.toFixed(1)}s)
                      </span>
                    </div>
                  );
                })}
              </div>

              {/* Projected Overlays Track */}
              {project.projected_overlays && project.projected_overlays.length > 0 && (
                <div style={{ marginTop: '6px', height: '22px', background: '#070a10', borderRadius: '4px', border: '1px solid #1e293b', position: 'relative', overflow: 'hidden' }}>
                  {project.projected_overlays.map((ov) => {
                    const left = (ov.timeline_start / (totalDuration || 1)) * 100;
                    const width = ((ov.timeline_end - ov.timeline_start) / (totalDuration || 1)) * 100;
                    return (
                      <div
                        key={ov.overlay_id}
                        onClick={() => handleSeekVirtual(ov.timeline_start)}
                        title={`Caption: '${ov.content}' [${ov.timeline_start}s - ${ov.timeline_end}s]`}
                        style={{
                          position: 'absolute',
                          left: `${left}%`,
                          width: `${Math.max(width, 3)}%`,
                          height: '100%',
                          background: ov.color === '#FF2A2A' ? '#ef4444' : '#10b981',
                          borderRadius: '3px',
                          display: 'flex',
                          alignItems: 'center',
                          justifyContent: 'center',
                          fontSize: '0.65rem',
                          fontWeight: '700',
                          color: '#fff',
                          cursor: 'pointer',
                          whiteSpace: 'nowrap',
                          padding: '0 4px',
                        }}
                      >
                        💬 {ov.content}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>

            {/* Deterministic Text-Based Selection & Action Toolbar */}
            <div style={{
              padding: '10px 20px',
              background: 'rgba(15, 23, 42, 0.75)',
              borderBottom: '1px solid var(--border-subtle)',
              display: 'flex',
              flexWrap: 'wrap',
              alignItems: 'center',
              justifyContent: 'space-between',
              gap: '10px'
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.82rem' }}>
                <span style={{ fontWeight: '700', color: '#f1f5f9' }}>Selected Range:</span>
                <input
                  type="number"
                  step="0.05"
                  placeholder="Start (s)"
                  value={manualStart}
                  onChange={(e) => setManualStart(e.target.value)}
                  style={{
                    width: '80px',
                    background: '#0f172a',
                    border: '1.5px solid #64748b',
                    borderRadius: '6px',
                    padding: '6px 8px',
                    color: '#fff',
                    fontSize: '0.82rem',
                    fontWeight: '600',
                    fontFamily: 'var(--font-mono)'
                  }}
                />
                <span style={{ color: 'var(--text-faint)', fontWeight: '600' }}>to</span>
                <input
                  type="number"
                  step="0.05"
                  placeholder="End (s)"
                  value={manualEnd}
                  onChange={(e) => setManualEnd(e.target.value)}
                  style={{
                    width: '80px',
                    background: '#0f172a',
                    border: '1.5px solid #64748b',
                    borderRadius: '6px',
                    padding: '6px 8px',
                    color: '#fff',
                    fontSize: '0.82rem',
                    fontWeight: '600',
                    fontFamily: 'var(--font-mono)'
                  }}
                />
                <button
                  className="btn-secondary"
                  onClick={() => {
                    setManualStart(sourceTime.toFixed(2));
                  }}
                  style={{ padding: '5px 10px', fontSize: '0.76rem', fontWeight: '600' }}
                  title="Set Start to current playback position"
                >
                  ⏱ Set Start
                </button>
                <button
                  className="btn-secondary"
                  onClick={() => {
                    setManualEnd(sourceTime.toFixed(2));
                  }}
                  style={{ padding: '5px 10px', fontSize: '0.76rem', fontWeight: '600' }}
                  title="Set End to current playback position"
                >
                  ⏱ Set End
                </button>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                <button
                  className="btn-danger"
                  disabled={!manualStart || !manualEnd || loading}
                  onClick={() => executeDeterministicEdit('cut')}
                  title="Remove selected range deterministically from the video"
                >
                  ✂ Cut Selected
                </button>
                <button
                  className="btn-amber"
                  disabled={!manualStart || !manualEnd || loading}
                  onClick={() => executeDeterministicEdit('mute')}
                  title="Mute audio for the selected range"
                >
                  🔇 Mute Selected
                </button>
                <button
                  className="btn-emerald"
                  disabled={!manualStart || !manualEnd || loading}
                  onClick={() => setIsCaptionModalOpen(true)}
                  title="Add a caption anchored to this range"
                >
                  💬 Add Caption
                </button>
                {(manualStart || manualEnd) && (
                  <button className="btn-secondary" onClick={clearSelection} style={{ padding: '6px 12px', fontSize: '0.78rem' }}>
                    ✕ Clear
                  </button>
                )}
              </div>
            </div>

            {/* Before / After Preview Feedback Banner */}
            {lastEditFeedback && (
              <div style={{
                padding: '8px 20px',
                background: 'rgba(16, 185, 129, 0.12)',
                borderBottom: '1px solid rgba(16, 185, 129, 0.3)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                fontSize: '0.78rem',
                color: '#6ee7b7'
              }}>
                <span>
                  ✓ Applied <strong>{lastEditFeedback.operation.toUpperCase()}</strong>:
                  {' '}Duration changed from <strong>{lastEditFeedback.before.toFixed(2)}s</strong> → <strong>{lastEditFeedback.after.toFixed(2)}s</strong>
                  {' '}({lastEditFeedback.delta < 0 ? `${lastEditFeedback.delta.toFixed(2)}s cut` : 'no duration change'}).
                </span>
                <span style={{ fontFamily: 'var(--font-mono)', fontSize: '0.72rem', color: '#a7f3d0' }}>
                  Hash: {lastEditFeedback.hash.substring(0, 8)}
                </span>
              </div>
            )}

            {/* Interactive Word & Segment Transcript View */}
            <div style={{ flex: 1, padding: '16px 20px', overflowY: 'auto' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '0.8rem', fontWeight: '700', textTransform: 'uppercase', color: 'var(--text-muted)', letterSpacing: '0.04em' }}>
                    Transcript ({project.transcript?.words?.length || 0} tokens, {project.transcript?.segments?.length || 0} segments)
                  </span>
                  <span style={{ fontSize: '0.72rem', color: '#a5b4fc', background: 'rgba(99, 102, 241, 0.15)', padding: '2px 6px', borderRadius: '4px' }}>
                    {project.transcript?.language_detected?.toUpperCase()} → {project.transcript?.transcript_language?.toUpperCase()} ({project.transcript?.task})
                  </span>
                </div>

                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.75rem' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', color: 'var(--text-muted)' }}>
                    <input type="checkbox" checked={autoScroll} onChange={(e) => setAutoScroll(e.target.checked)} />
                    <span>Auto-scroll</span>
                  </label>
                  <div style={{ display: 'flex', border: '1px solid #334155', borderRadius: '4px', overflow: 'hidden' }}>
                    <button
                      onClick={() => setTranscriptViewMode('segments')}
                      style={{
                        background: transcriptViewMode === 'segments' ? '#4f46e5' : 'transparent',
                        color: '#fff',
                        border: 'none',
                        padding: '3px 8px',
                        cursor: 'pointer',
                        fontSize: '0.72rem'
                      }}
                    >
                      Segments
                    </button>
                    <button
                      onClick={() => setTranscriptViewMode('flow')}
                      style={{
                        background: transcriptViewMode === 'flow' ? '#4f46e5' : 'transparent',
                        color: '#fff',
                        border: 'none',
                        padding: '3px 8px',
                        cursor: 'pointer',
                        fontSize: '0.72rem'
                      }}
                    >
                      Flow
                    </button>
                  </div>
                </div>
              </div>

              {/* Empty Transcript State */}
              {(!project.transcript || (!project.transcript.segments?.length && !project.transcript.words?.length)) && (
                <div style={{ padding: '30px 20px', textAlign: 'center', color: 'var(--text-faint)', fontSize: '0.85rem' }}>
                  No spoken speech detected in this media file. Audio track may be silent or instrumental.
                </div>
              )}

              {/* View 1: Timestamped Segments View */}
              {transcriptViewMode === 'segments' && project.transcript?.segments?.map((seg, sIdx) => {
                const isSegActive = sourceTime >= seg.start && sourceTime <= seg.end;
                const isSegRetained = isSourceTimeInTimeline((seg.start + seg.end) / 2, project.timeline?.segments);

                return (
                  <div key={seg.id || sIdx} className={`segment-card ${isSegActive ? 'active' : ''} ${!isSegRetained ? 'cut-segment' : ''}`}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                        <button
                          onClick={() => handleSeek(seg.start)}
                          style={{
                            background: isSegRetained ? 'rgba(99, 102, 241, 0.2)' : 'rgba(239, 68, 68, 0.15)',
                            color: isSegRetained ? '#a5b4fc' : '#fca5a5',
                            border: `1px solid ${isSegRetained ? 'rgba(99, 102, 241, 0.4)' : 'rgba(239, 68, 68, 0.4)'}`,
                            borderRadius: '4px',
                            padding: '2px 6px',
                            fontFamily: 'var(--font-mono)',
                            fontSize: '0.72rem',
                            cursor: 'pointer',
                          }}
                          title="Click timestamp to seek video"
                        >
                          ▶ {formatTime(seg.start)} - {formatTime(seg.end)}
                        </button>
                        <span style={{ fontSize: '0.72rem', color: 'var(--text-faint)' }}>
                          {seg.speaker || 'speaker_0'}
                        </span>
                        {!isSegRetained && (
                          <span style={{ fontSize: '0.65rem', color: '#f87171', background: 'rgba(239, 68, 68, 0.2)', padding: '1px 5px', borderRadius: '3px', fontWeight: '700' }}>
                            CUT
                          </span>
                        )}
                      </div>
                      <button
                        className="btn-secondary"
                        style={{ padding: '2px 6px', fontSize: '0.7rem' }}
                        onClick={() => handleSelectSegment(seg)}
                        title="Select this segment for cutting or muting"
                      >
                        Select Segment
                      </button>
                    </div>

                    {/* Word tokens or fallback text */}
                    {seg.words && seg.words.length > 0 ? (
                      <div style={{ lineHeight: '1.8' }}>
                        {seg.words.map((w, wIdx) => {
                          const isWordActive = sourceTime >= w.start && sourceTime <= w.end;
                          const isWordRetained = isSourceTimeInTimeline(w.start, project.timeline?.segments);
                          const isSelected = selectedWordStart && selectedWordEnd &&
                            w.start >= parseFloat(manualStart) - 0.01 && w.end <= parseFloat(manualEnd) + 0.01;

                          return (
                            <span
                              key={w.id || `${sIdx}_${wIdx}`}
                              ref={isWordActive ? activeWordRef : null}
                              className={`word-pill ${isWordActive ? 'active' : ''} ${isSelected ? 'selected' : ''} ${!isWordRetained ? 'cut' : ''}`}
                              onClick={(e) => handleWordClick({ id: w.id || `w_${sIdx}_${wIdx}`, word: w.text, start_s: w.start, end_s: w.end }, e)}
                              title={!isWordRetained
                                ? `[Cut from video] '${w.text}' [${w.start.toFixed(2)}s - ${w.end.toFixed(2)}s]`
                                : `[${w.start.toFixed(2)}s - ${w.end.toFixed(2)}s] Click to seek, Shift+Click to range`}
                            >
                              {w.text}
                            </span>
                          );
                        })}
                      </div>
                    ) : (
                      <p
                        onClick={() => handleSeek(seg.start)}
                        style={{
                          fontSize: '0.86rem',
                          color: isSegRetained ? '#cbd5e1' : '#94a3b8',
                          textDecoration: isSegRetained ? 'none' : 'line-through',
                          cursor: 'pointer',
                          lineHeight: '1.5'
                        }}
                        title="Click to seek"
                      >
                        {seg.text}
                      </p>
                    )}
                  </div>
                );
              })}

              {/* View 2: Continuous Flow of Word Pills */}
              {transcriptViewMode === 'flow' && (
                <div style={{
                  lineHeight: '2.0',
                  background: 'rgba(15, 23, 42, 0.4)',
                  padding: '16px',
                  borderRadius: '8px',
                  border: '1px solid var(--border-subtle)'
                }}>
                  {project.transcript?.words?.map((w) => {
                    const isWordActive = sourceTime >= w.start_s && sourceTime <= w.end_s;
                    const isWordRetained = isSourceTimeInTimeline(w.start_s, project.timeline?.segments);
                    const isSelected = selectedWordStart && selectedWordEnd &&
                      w.start_s >= parseFloat(manualStart) - 0.01 && w.end_s <= parseFloat(manualEnd) + 0.01;

                    return (
                      <span
                        key={w.id}
                        ref={isWordActive ? activeWordRef : null}
                        className={`word-pill ${isWordActive ? 'active' : ''} ${isSelected ? 'selected' : ''} ${!isWordRetained ? 'cut' : ''}`}
                        onClick={(e) => handleWordClick(w, e)}
                        title={!isWordRetained
                          ? `[Cut from video] '${w.word}' [${w.start_s.toFixed(2)}s - ${w.end_s.toFixed(2)}s]`
                          : `[${w.start_s.toFixed(2)}s - ${w.end_s.toFixed(2)}s] Click to seek, Shift+Click to select`}
                      >
                        {w.word}
                      </span>
                    );
                  })}
                </div>
              )}

              {/* View 3: Clean informative fallback when no speech was detected */}
              {(!project.transcript?.segments || project.transcript.segments.length === 0) &&
               (!project.transcript?.words || project.transcript.words.length === 0) && (
                <div style={{
                  padding: '24px 20px',
                  background: 'rgba(30, 41, 59, 0.4)',
                  borderRadius: '10px',
                  border: '1.5px dashed #475569',
                  textAlign: 'center',
                  color: '#cbd5e1'
                }}>
                  <div style={{ fontSize: '1.8rem', marginBottom: '8px' }}>🎙️</div>
                  <h4 style={{ fontSize: '0.95rem', fontWeight: '700', color: '#fff', marginBottom: '4px' }}>
                    No Spoken Words Detected in this Video
                  </h4>
                  <p style={{ fontSize: '0.78rem', color: '#94a3b8', maxWidth: '480px', margin: '0 auto 14px', lineHeight: '1.5' }}>
                    Speech-to-text transcribes human speech. If this clip contains tones, beeps, or silence, you can still edit it precisely using timestamps or the Assistant!
                  </p>
                  <div style={{ display: 'flex', gap: '8px', justifyContent: 'center', flexWrap: 'wrap' }}>
                    <button
                      className="btn-secondary"
                      style={{ fontSize: '0.74rem' }}
                      onClick={() => proposeAssistantEdit("Mute audio")}
                    >
                      🔇 Mute Entire Audio
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ fontSize: '0.74rem' }}
                      onClick={() => proposeAssistantEdit(`Cut the first ${Math.min(2, Math.floor(project.timeline.duration_s / 2))} seconds`)}
                    >
                      ✂ Cut Start
                    </button>
                    <button
                      className="btn-secondary"
                      style={{ fontSize: '0.74rem' }}
                      onClick={() => {
                        setManualStart('0.00');
                        setManualEnd(project.timeline.duration_s.toFixed(2));
                        setIsCaptionModalOpen(true);
                      }}
                    >
                      💬 Add Caption Overlay
                    </button>
                  </div>
                </div>
              )}
            </div>

          </div>

          {/* Right Column: Natural-Language Editing Assistant & History Panel */}
          <div style={{ display: 'flex', flexDirection: 'column', background: 'var(--bg-secondary)', overflow: 'hidden' }}>
            
            {/* Header */}
            <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border-subtle)' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                <h2 style={{ fontSize: '0.9rem', fontWeight: '700', color: '#fff', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <span>✨</span> Natural-Language Assistant
                </h2>
                <span style={{ fontSize: '0.68rem', color: '#818cf8', background: 'rgba(99, 102, 241, 0.15)', padding: '2px 6px', borderRadius: '4px' }}>
                  Preview & Confirm
                </span>
              </div>
              <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: '2px' }}>
                Converts natural requests to strict timestamped EDL proposals
              </p>
            </div>

            {/* Quick Prompts Bar */}
            <div style={{ padding: '10px 16px', background: 'rgba(10, 14, 23, 0.7)', borderBottom: '1.5px solid #334155' }}>
              <span style={{ fontSize: '0.72rem', color: '#94a3b8', fontWeight: '600', display: 'block', marginBottom: '6px' }}>
                💡 Quick Example Prompts:
              </span>
              <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap' }}>
                {(project.timeline.duration_s <= 10 ? [
                  "Mute audio",
                  "Cut the first 2 seconds",
                  "Add captions to the entire video.",
                  "Undo"
                ] : [
                  "Remove the sentence where I say we will discuss pricing.",
                  "Mute the audio from 10 to 15 seconds.",
                  "Add captions to the entire video.",
                  "Remove the long pause after the introduction.",
                  "Undo"
                ]).map((step, idx) => (
                  <button
                    key={idx}
                    className="quick-prompt-pill"
                    onClick={() => proposeAssistantEdit(step)}
                    disabled={assistantLoading || loading}
                  >
                    {step}
                  </button>
                ))}
              </div>
            </div>

            {/* Main Feed */}
            <div style={{ flex: 1, padding: '14px 16px', overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '12px' }}>
              
              {/* Proposed Edit Preview Card (Requires Confirmation Before Applying) */}
              {activeProposal && (
                <div style={{
                  background: 'rgba(99, 102, 241, 0.08)',
                  border: '1px solid rgba(99, 102, 241, 0.4)',
                  borderRadius: '10px',
                  padding: '14px',
                  boxShadow: '0 4px 20px rgba(0, 0, 0, 0.3)'
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
                    <span style={{
                      background: 'rgba(99, 102, 241, 0.25)',
                      color: '#a5b4fc',
                      border: '1px solid #6366f1',
                      padding: '2px 8px',
                      borderRadius: '4px',
                      fontSize: '0.7rem',
                      fontWeight: '700',
                      letterSpacing: '0.04em'
                    }}>
                      PROPOSAL PREVIEW
                    </span>
                    <span style={{ fontSize: '0.7rem', color: '#cbd5e1' }}>
                      Confirmation Required
                    </span>
                  </div>

                  <p style={{ fontSize: '0.82rem', fontWeight: '600', color: '#fff', marginBottom: '4px' }}>
                    "{activeProposal.instruction || activeProposal.user_instruction}"
                  </p>
                  <p style={{ fontSize: '0.75rem', color: '#cbd5e1', marginBottom: '10px' }}>
                    {activeProposal.summary || activeProposal.explanation}
                  </p>

                  {/* Preview Metrics Grid */}
                  <div style={{
                    display: 'grid',
                    gridTemplateColumns: 'repeat(3, 1fr)',
                    gap: '8px',
                    background: 'rgba(15, 23, 42, 0.7)',
                    padding: '8px 10px',
                    borderRadius: '6px',
                    marginBottom: '10px',
                    border: '1px solid var(--border-subtle)'
                  }}>
                    <div>
                      <span style={{ fontSize: '0.65rem', color: 'var(--text-faint)', display: 'block' }}>Before</span>
                      <span style={{ fontSize: '0.8rem', fontWeight: '700', color: '#94a3b8' }}>
                        {activeProposal.preview?.before_duration_s?.toFixed(2)}s
                      </span>
                    </div>
                    <div>
                      <span style={{ fontSize: '0.65rem', color: 'var(--text-faint)', display: 'block' }}>After</span>
                      <span style={{ fontSize: '0.8rem', fontWeight: '700', color: '#38bdf8' }}>
                        {activeProposal.preview?.after_duration_s?.toFixed(2)}s
                      </span>
                    </div>
                    <div>
                      <span style={{ fontSize: '0.65rem', color: 'var(--text-faint)', display: 'block' }}>Duration Change</span>
                      <span style={{
                        fontSize: '0.8rem',
                        fontWeight: '700',
                        color: (activeProposal.preview?.duration_delta_s || 0) < 0 ? '#10b981' : (activeProposal.preview?.duration_delta_s || 0) > 0 ? '#f59e0b' : '#94a3b8'
                      }}>
                        {(activeProposal.preview?.duration_delta_s || 0) > 0 ? '+' : ''}
                        {activeProposal.preview?.duration_delta_s?.toFixed(2)}s
                      </span>
                    </div>
                  </div>

                  {/* Operations List */}
                  <div style={{ marginBottom: '12px' }}>
                    <span style={{ fontSize: '0.68rem', color: 'var(--text-faint)', fontWeight: '600', display: 'block', marginBottom: '4px' }}>
                      Proposed Operations ({activeProposal.operations?.length || 0}):
                    </span>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                      {activeProposal.operations?.map((op, i) => {
                        const opType = (op.operation || op.type || '').toLowerCase();
                        const sStart = op.start_time ?? op.source_start ?? 0;
                        const sEnd = op.end_time ?? op.source_end ?? 0;
                        const targetText = op.phrase ? `"${op.phrase}"` : op.content ? `"${op.content}"` : op.rationale || op.target || '';

                        return (
                          <div key={i} style={{
                            background: 'rgba(30, 41, 59, 0.6)',
                            padding: '5px 8px',
                            borderRadius: '4px',
                            fontSize: '0.72rem',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'space-between',
                            gap: '6px'
                          }}>
                            <span style={{
                              fontWeight: '700',
                              color: opType.includes('cut') ? '#ef4444' : opType.includes('mute') ? '#f59e0b' : '#38bdf8',
                              textTransform: 'uppercase',
                              fontSize: '0.65rem'
                            }}>
                              {opType.replace('_', ' ')}
                            </span>
                            <span style={{ fontFamily: 'var(--font-mono)', color: '#a5b4fc', fontSize: '0.7rem' }}>
                              [{sStart.toFixed(2)}s - {sEnd.toFixed(2)}s]
                            </span>
                            <span style={{ color: 'var(--text-muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '140px' }} title={targetText}>
                              {targetText}
                            </span>
                          </div>
                        );
                      })}
                    </div>
                  </div>

                  {/* Confirmation Actions */}
                  <div style={{ display: 'flex', gap: '8px' }}>
                    <button
                      className="btn-primary"
                      onClick={applyAssistantProposal}
                      disabled={loading}
                      style={{ flex: 1, padding: '7px 12px', fontSize: '0.78rem' }}
                    >
                      ✓ Confirm & Apply Edit
                    </button>
                    <button
                      className="btn-secondary"
                      onClick={discardAssistantProposal}
                      disabled={loading}
                      style={{ padding: '7px 12px', fontSize: '0.78rem' }}
                    >
                      ✕ Discard
                    </button>
                  </div>
                </div>
              )}

              {/* Active Clarification Card (if pending) */}
              {pendingClarification && (
                <div style={{
                  background: 'rgba(245, 158, 11, 0.12)',
                  border: '1px solid rgba(245, 158, 11, 0.4)',
                  borderRadius: '10px',
                  padding: '14px',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
                    <span className="badge-clarification">Ambiguous Request</span>
                  </div>
                  <p style={{ fontSize: '0.82rem', fontWeight: '600', color: '#fef3c7', marginBottom: '8px' }}>
                    {pendingClarification.question}
                  </p>
                  <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '8px' }}>
                    Multiple transcript matches found. Select an option to proceed:
                  </p>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                    {pendingClarification.options?.map((opt, i) => (
                      <button
                        key={i}
                        className="btn-secondary"
                        style={{
                          background: 'rgba(245, 158, 11, 0.2)',
                          borderColor: '#f59e0b',
                          color: '#fff',
                          fontSize: '0.75rem',
                          textAlign: 'left',
                          justifyContent: 'flex-start',
                          padding: '6px 10px'
                        }}
                        onClick={() => proposeAssistantEdit(opt)}
                      >
                        👉 {opt}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {/* History Items */}
              {history.map((item, idx) => (
                <div
                  key={idx}
                  style={{
                    background: 'var(--bg-card)',
                    border: '1px solid var(--border-subtle)',
                    borderRadius: '8px',
                    padding: '10px 12px',
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '4px' }}>
                    <span style={{ fontSize: '0.78rem', fontWeight: '600', color: '#cbd5e1' }}>
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
                  <p style={{ fontSize: '0.76rem', color: 'var(--text-muted)' }}>
                    {item.result.summary || item.result.explanation}
                  </p>
                  {item.result.status === 'applied' && (
                    <div style={{ marginTop: '4px', fontSize: '0.7rem', color: 'var(--text-faint)', display: 'flex', gap: '10px' }}>
                      <span>Duration: {item.result.duration_s?.toFixed(2)}s</span>
                      <span>Hash: {item.result.timeline_hash?.substring(0, 8)}</span>
                      {item.result.version && <span>v{item.result.version}</span>}
                    </div>
                  )}
                </div>
              ))}

              {history.length === 0 && !activeProposal && !pendingClarification && (
                <div style={{ textAlign: 'center', padding: '30px 10px', color: 'var(--text-faint)', fontSize: '0.8rem' }}>
                  Ask the assistant to cut sentences, mute ranges, remove pauses, or add captions.
                </div>
              )}
            </div>

            {/* Render Job Banner */}
            {renderJob && (
              <div style={{ padding: '12px 16px', background: 'rgba(99, 102, 241, 0.15)', borderTop: '1px solid rgba(99, 102, 241, 0.3)' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px', fontSize: '0.78rem' }}>
                  <span>Render Status: <strong>{renderJob.status}</strong></span>
                  <span>{renderJob.progress}%</span>
                </div>
                <div style={{ height: '4px', background: '#1e293b', borderRadius: '2px', overflow: 'hidden' }}>
                  <div style={{ height: '100%', width: `${renderJob.progress}%`, background: '#6366f1', transition: 'width 0.3s ease' }}></div>
                </div>
                {renderJob.result_links?.video_url && (
                  <div style={{ marginTop: '8px', display: 'flex', gap: '8px' }}>
                    <a href={renderJob.result_links.video_url} download className="btn-primary" style={{ fontSize: '0.75rem', padding: '4px 10px' }}>
                      📥 Download Rendered MP4
                    </a>
                    <a href={renderJob.result_links.manifest_url} download className="btn-secondary" style={{ fontSize: '0.75rem', padding: '4px 10px' }}>
                      View Manifest
                    </a>
                  </div>
                )}
              </div>
            )}

            {/* Natural-Language Prompt Input Bar */}
            <div style={{ padding: '14px 16px', borderTop: '1.5px solid #334155', background: '#090d16' }}>
              <form onSubmit={(e) => { e.preventDefault(); proposeAssistantEdit(); }} style={{ display: 'flex', gap: '8px' }}>
                <input
                  type="text"
                  placeholder="e.g. 'Mute audio', 'Cut first 2 seconds'..."
                  value={instruction}
                  onChange={(e) => setInstruction(e.target.value)}
                  disabled={assistantLoading}
                  style={{
                    flex: 1,
                    background: '#0f172a',
                    border: '1.5px solid #475569',
                    borderRadius: '8px',
                    padding: '10px 14px',
                    color: '#fff',
                    fontSize: '0.85rem',
                    outline: 'none',
                  }}
                />
                <button
                  type="submit"
                  className="btn-primary"
                  disabled={assistantLoading || !instruction.trim()}
                  style={{ padding: '10px 18px', fontWeight: '700' }}
                >
                  {assistantLoading ? 'Analyzing...' : '✨ Propose Edit'}
                </button>
              </form>
            </div>

          </div>
        </main>
      )}

      {/* Add Caption Modal */}
      {isCaptionModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 0, 0, 0.75)',
          backdropFilter: 'blur(4px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 100
        }}>
          <div className="glass-panel" style={{ width: '400px', padding: '24px' }}>
            <h3 style={{ fontSize: '1rem', fontWeight: '700', marginBottom: '12px' }}>Add Caption Overlay</h3>
            <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '14px' }}>
              Range: {manualStart}s - {manualEnd}s
            </p>

            <div style={{ marginBottom: '12px' }}>
              <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-faint)', marginBottom: '4px' }}>
                Caption Text:
              </label>
              <input
                type="text"
                value={captionText}
                onChange={(e) => setCaptionText(e.target.value)}
                placeholder="e.g. Welcome to IncuBrix!"
                style={{
                  width: '100%',
                  background: '#090d16',
                  border: '1px solid #334155',
                  borderRadius: '6px',
                  padding: '8px 10px',
                  color: '#fff',
                  fontSize: '0.85rem'
                }}
              />
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '18px' }}>
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-faint)', marginBottom: '4px' }}>
                  Color:
                </label>
                <input
                  type="color"
                  value={captionColor}
                  onChange={(e) => setCaptionColor(e.target.value)}
                  style={{ width: '100%', height: '34px', background: '#090d16', border: '1px solid #334155', borderRadius: '4px', cursor: 'pointer' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: '0.75rem', color: 'var(--text-faint)', marginBottom: '4px' }}>
                  Position:
                </label>
                <select
                  value={captionPosition}
                  onChange={(e) => setCaptionPosition(e.target.value)}
                  style={{ width: '100%', height: '34px', background: '#090d16', color: '#fff', border: '1px solid #334155', borderRadius: '4px', padding: '0 6px', fontSize: '0.75rem' }}
                >
                  <option value="bottom">Bottom</option>
                  <option value="top">Top</option>
                </select>
              </div>
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
              <button className="btn-secondary" onClick={() => setIsCaptionModalOpen(false)}>
                Cancel
              </button>
              <button
                className="btn-primary"
                disabled={!captionText.trim()}
                onClick={() => {
                  executeDeterministicEdit('caption', {
                    content: captionText,
                    color: captionColor,
                    position: captionPosition
                  });
                  setIsCaptionModalOpen(false);
                  setCaptionText('');
                }}
              >
                Add Caption
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Version History & Restoration Modal */}
      {isVersionModalOpen && (
        <div style={{
          position: 'fixed',
          top: 0,
          left: 0,
          right: 0,
          bottom: 0,
          background: 'rgba(0, 0, 0, 0.75)',
          backdropFilter: 'blur(5px)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 100
        }}>
          <div className="glass-panel" style={{ width: '560px', maxHeight: '80vh', display: 'flex', flexDirection: 'column', padding: '24px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
              <h3 style={{ fontSize: '1.05rem', fontWeight: '700', color: '#fff' }}>Project Version History</h3>
              <button
                onClick={() => setIsVersionModalOpen(false)}
                style={{ background: 'none', border: 'none', color: 'var(--text-muted)', fontSize: '1.1rem', cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>
            <p style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginBottom: '16px' }}>
              Each accepted edit is recorded as an immutable versioned timeline state snapshot.
            </p>

            <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '8px', paddingRight: '4px', marginBottom: '16px' }}>
              {versionList.map((ver) => (
                <div
                  key={ver.version}
                  style={{
                    background: ver.is_current ? 'rgba(99, 102, 241, 0.15)' : 'rgba(15, 23, 42, 0.6)',
                    border: ver.is_current ? '1px solid #6366f1' : '1px solid var(--border-subtle)',
                    borderRadius: '8px',
                    padding: '10px 14px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    gap: '12px'
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
                      <span style={{ fontFamily: 'var(--font-mono)', fontWeight: '700', fontSize: '0.8rem', color: '#a5b4fc' }}>
                        v{ver.version}
                      </span>
                      {ver.is_current && (
                        <span className="badge-applied">Active State</span>
                      )}
                      <span style={{ fontSize: '0.75rem', fontWeight: '600', color: '#e2e8f0' }}>
                        {ver.instruction === '__initial__' ? 'Initial Untouched Media' : ver.instruction}
                      </span>
                    </div>
                    <p style={{ fontSize: '0.72rem', color: 'var(--text-muted)', marginBottom: '2px' }}>
                      {ver.summary}
                    </p>
                    <div style={{ display: 'flex', gap: '10px', fontSize: '0.7rem', color: 'var(--text-faint)' }}>
                      <span>Duration: {ver.duration_s.toFixed(2)}s</span>
                      <span>Hash: {ver.timeline_hash.substring(0, 8)}</span>
                    </div>
                  </div>

                  <div>
                    <button
                      className="btn-secondary"
                      disabled={ver.is_current || loading}
                      onClick={() => handleRestoreVersion(ver.version)}
                      style={{
                        padding: '4px 10px',
                        fontSize: '0.72rem',
                        borderColor: ver.is_current ? 'transparent' : '#6366f1',
                        color: ver.is_current ? 'var(--text-faint)' : '#fff'
                      }}
                    >
                      {ver.is_current ? 'Current' : 'Restore'}
                    </button>
                  </div>
                </div>
              ))}
            </div>

            <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
              <button className="btn-secondary" onClick={() => setIsVersionModalOpen(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
