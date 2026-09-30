import { ChangeEvent, useEffect, useMemo, useRef, useState } from "react";
import { ingestVoice } from "../lib/api";
import type { VoiceEntity, VoiceResult } from "../types";
import ErrorBoundary from "./ErrorBoundary";
import Skeleton from "./Skeleton";

type Phase = "idle" | "recording" | "transcribing" | "done" | "error";

const CHIP_CLASS: Record<string, string> = { person: "ent-person", phone: "ent-phone", location: "ent-location", legal_section: "ent-section" };
const CHIP_LABEL: Record<string, string> = { person: "Person", phone: "Phone", location: "Location", legal_section: "IPC/BNS section" };
const SHOWN = new Set(Object.keys(CHIP_CLASS));

/** Split the transcript into plain and highlighted runs (first occurrence of each entity, no overlaps). */
function highlight(transcript: string, entities: VoiceEntity[]) {
  const lower = transcript.toLowerCase();
  const spans: { start: number; end: number; e: VoiceEntity }[] = [];
  for (const e of entities.filter((x) => SHOWN.has(x.type))) {
    for (const needle of [e.type === "legal_section" ? e.evidence : e.value, e.value]) {
      const start = needle ? lower.indexOf(needle.toLowerCase()) : -1;
      if (start >= 0) {
        if (!spans.some((s) => start < s.end && start + needle.length > s.start)) spans.push({ start, end: start + needle.length, e });
        break;
      }
    }
  }
  spans.sort((a, b) => a.start - b.start);
  const parts: { text: string; e?: VoiceEntity }[] = [];
  let at = 0;
  for (const s of spans) {
    if (s.start > at) parts.push({ text: transcript.slice(at, s.start) });
    parts.push({ text: transcript.slice(s.start, s.end), e: s.e });
    at = s.end;
  }
  if (at < transcript.length) parts.push({ text: transcript.slice(at) });
  return parts;
}

function Inner({ onUseTranscript }: { onUseTranscript: (transcript: string, result: VoiceResult) => void }) {
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState("");
  const [seconds, setSeconds] = useState(0);
  const [result, setResult] = useState<VoiceResult | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<number | undefined>(undefined);
  const canRecord = typeof navigator !== "undefined" && !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined";

  useEffect(() => () => {
    window.clearInterval(timer.current);
    recorder.current?.stream.getTracks().forEach((t) => t.stop());
  }, []);

  async function send(blob: Blob, name: string) {
    setPhase("transcribing");
    setError("");
    try {
      const res = await ingestVoice(blob, name);
      setResult(res);
      setPhase("done");
      onUseTranscript(res.transcript, res); // pre-fill the FIR text form; the officer can still edit it
    } catch (err) {
      setError(err instanceof Error ? err.message : "Transcription failed.");
      setPhase("error");
    }
  }

  async function start() {
    setError("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const rec = new MediaRecorder(stream);
      chunks.current = [];
      rec.ondataavailable = (ev) => ev.data.size && chunks.current.push(ev.data);
      rec.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        window.clearInterval(timer.current);
        const type = rec.mimeType || "audio/webm";
        const ext = type.includes("mp4") ? "m4a" : type.includes("ogg") ? "ogg" : "webm";
        void send(new Blob(chunks.current, { type }), `voice-fir.${ext}`);
      };
      recorder.current = rec;
      rec.start();
      setSeconds(0);
      timer.current = window.setInterval(() => setSeconds((s) => s + 1), 1000);
      setPhase("recording");
    } catch {
      setError("Microphone unavailable or permission denied. You can upload an audio file instead.");
      setPhase("error");
    }
  }

  function stop() {
    recorder.current?.stop();
  }

  function pickFile(e: ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) void send(f, f.name);
    e.target.value = "";
  }

  const parts = useMemo(() => (result ? highlight(result.transcript, result.entities) : []), [result]);
  const chips = (result?.entities ?? []).filter((e) => SHOWN.has(e.type));

  return (
    <div className="voice-fir" aria-live="polite">
      <div className="page-actions">
        {phase !== "recording" ? (
          <button type="button" onClick={() => void start()} disabled={!canRecord || phase === "transcribing"}>🎙 Voice FIR</button>
        ) : (
          <button type="button" className="danger" onClick={stop}>■ Stop ({seconds}s)</button>
        )}
        <label className="secondary btn-link" style={{ cursor: "pointer" }}>
          Upload audio
          <input type="file" accept=".wav,.mp3,.m4a,audio/*" onChange={pickFile} style={{ display: "none" }} disabled={phase === "transcribing" || phase === "recording"} />
        </label>
      </div>
      {!canRecord && <p className="hint">This browser cannot record audio; upload a WAV, MP3 or M4A file.</p>}
      {phase === "recording" && <p className="hint"><span className="dot alert" /> Recording… speak the complaint, then press Stop.</p>}
      {phase === "transcribing" && (
        <div>
          <p className="hint">Transcribing offline and extracting entities — this can take about 30 seconds for a two-minute recording.</p>
          <Skeleton rows={3} />
        </div>
      )}
      {phase === "error" && (
        <div className="panel-error" role="alert">
          <span>{error}</span>
          <button type="button" className="link-btn" onClick={() => setPhase("idle")}>Retry</button>
        </div>
      )}
      {phase === "done" && result && (
        <div className="voice-result">
          <p className="voice-transcript" lang={result.language ?? undefined}>
            {parts.map((p, i) => (p.e ? <mark key={i} className={`ent ${CHIP_CLASS[p.e.type]}`} title={`${CHIP_LABEL[p.e.type]} · ${Math.round(p.e.confidence * 100)}%`}>{p.text}</mark> : <span key={i}>{p.text}</span>))}
          </p>
          <div className="suggest-group">
            {chips.map((e, i) => (
              <span key={`${e.type}-${e.value}-${i}`} className={`ent-chip ${CHIP_CLASS[e.type]}`} title={`${CHIP_LABEL[e.type]} · confidence ${Math.round(e.confidence * 100)}%`}>
                {CHIP_LABEL[e.type]}: {e.value}
              </span>
            ))}
            {!chips.length && <span className="hint">No entities recognised in the transcript.</span>}
          </div>
          <p className="lead-note">{result.message} The FIR text box below has been pre-filled — correct any transcription errors before extracting.</p>
        </div>
      )}
    </div>
  );
}

export default function VoiceFir(props: { onUseTranscript: (transcript: string, result: VoiceResult) => void }) {
  return (
    <ErrorBoundary label="voice recording">
      <Inner {...props} />
    </ErrorBoundary>
  );
}
