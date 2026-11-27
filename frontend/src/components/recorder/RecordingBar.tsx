"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  ArrowLeft,
  BookmarkPlus,
  Download,
  Mic,
  Pause,
  Play,
  Radio,
  ShieldAlert,
  Square,
} from "lucide-react";
import { api } from "@/lib/api";
import { formatOffsetMs } from "@/lib/markdown";
import type { ClientControlMessage } from "@/hooks/useSessionSocket";
import { useAudioCapture } from "@/hooks/useAudioCapture";
import { useSessionStore } from "@/store/sessionStore";

interface RecordingBarProps {
  sessionId: string;
  sendAudioChunk: (offsetMs: number, pcmBuffer: ArrayBuffer) => Promise<void> | void;
  sendControl: (payload: ClientControlMessage) => Promise<boolean>;
  onOpenExport: () => void;
}

const DEMO_UTTERANCES: Array<{
  speaker: string;
  text: string;
  confidence: number;
  forcePass1?: boolean;
}> = [
  {
    speaker: "Speaker 1",
    text: "Let's move to authentication and API gateway design for our Q4 launch.",
    confidence: 0.96,
  },
  {
    speaker: "Speaker 2",
    text: "We decided to adopt OAuth2 with PKCE and short-lived rotating JWT refresh tokens.",
    confidence: 0.97,
    forcePass1: true,
  },
  {
    speaker: "Speaker 1",
    text: "Next topic: database indexing and hybrid search latency targets.",
    confidence: 0.68,
  },
  {
    speaker: "Speaker 2",
    text: "Alice will benchmark pgvector HNSW recall and tsvector Reciprocal Rank Fusion by Friday.",
    confidence: 0.95,
    forcePass1: true,
  },
  {
    speaker: "Speaker 1",
    text: "Next topic: release timeline and mobile PWA checklist. Should we enable delete_after_processing by default?",
    confidence: 0.94,
    forcePass1: true,
  },
];

export function RecordingBar({
  sessionId,
  sendAudioChunk,
  sendControl,
  onOpenExport,
}: RecordingBarProps) {
  const status = useSessionStore((s) => s.status);
  const durationMs = useSessionStore((s) => s.durationMs);
  const wsConnected = useSessionStore((s) => s.wsConnected);
  const sttMode = useSessionStore((s) => s.sttMode);
  const bufferedChunkCount = useSessionStore((s) => s.bufferedChunkCount);
  const hydrateSession = useSessionStore((s) => s.hydrateSession);

  const [pinToast, setPinToast] = useState<string | null>(null);
  const [simulating, setSimulating] = useState(false);
  const [redacting, setRedacting] = useState(false);
  const redactDialogRef = useRef<HTMLDialogElement | null>(null);

  const {
    isRecording,
    isPaused,
    isSpeechActive,
    vadEngine,
    micLevel,
    elapsedMs,
    micError,
    currentOffsetMs,
    start,
    pause,
    resume,
    stop,
  } = useAudioCapture({
    initialElapsedMs: durationMs,
    onSpeechChunk: (offsetMs, buf) => {
      void sendAudioChunk(offsetMs, buf);
    },
  });

  const handlePinMoment = useCallback(async () => {
    const offset = isRecording ? currentOffsetMs() : durationMs;
    const label = `Pinned at ${formatOffsetMs(offset)}`;
    const sent = await sendControl({
      type: "pin_moment",
      offset_ms: offset,
      label,
    });
    if (!sent) {
      await api.pinMoment(sessionId, offset, label);
    }
    setPinToast(`Pinned ${formatOffsetMs(offset)}`);
    setTimeout(() => setPinToast(null), 2200);
  }, [currentOffsetMs, durationMs, isRecording, sendControl, sessionId]);

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.shiftKey && e.key.toLowerCase() === "p") {
        e.preventDefault();
        void handlePinMoment();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [handlePinMoment]);

  useEffect(() => {
    const dialog = redactDialogRef.current;
    if (!dialog || "closedBy" in HTMLDialogElement.prototype) return;

    let pressedOutside = false;
    const outside = (e: PointerEvent) => {
      const r = dialog.getBoundingClientRect();
      return (
        e.clientX < r.left ||
        e.clientX > r.right ||
        e.clientY < r.top ||
        e.clientY > r.bottom
      );
    };
    const onDown = (e: PointerEvent) => {
      pressedOutside = outside(e);
    };
    const onUp = (e: PointerEvent) => {
      if (pressedOutside && outside(e)) dialog.close();
    };
    dialog.addEventListener("pointerdown", onDown);
    dialog.addEventListener("pointerup", onUp);
    return () => {
      dialog.removeEventListener("pointerdown", onDown);
      dialog.removeEventListener("pointerup", onUp);
    };
  }, []);

  const handleStopAndConsolidate = async () => {
    if (isRecording) {
      await stop();
    }
    const sent = await sendControl({ type: "stop_session" });
    if (!sent) {
      const updated = await api.stopSession(sessionId, true);
      hydrateSession(updated);
    }
  };

  const handleConfirmRedact = async () => {
    setRedacting(true);
    try {
      const offset = isRecording ? currentOffsetMs() : durationMs;
      const sent = await sendControl({
        type: "redact_window",
        window_ms: 120_000,
        current_offset_ms: offset,
      });
      if (!sent) {
        await api.redactWindow(sessionId, 120_000, offset);
        const refreshed = await api.getSession(sessionId);
        hydrateSession(refreshed);
      }
      redactDialogRef.current?.close();
    } finally {
      setRedacting(false);
    }
  };

  const handleSimulateConversation = async () => {
    if (simulating) return;
    setSimulating(true);
    try {
      let cursor = Math.max(currentOffsetMs(), durationMs);
      for (const item of DEMO_UTTERANCES) {
        const startMs = cursor + 800;
        const endMs = startMs + 4200;
        cursor = endMs;
        await sendControl({
          type: "simulate_utterance",
          speaker_label: item.speaker,
          text: item.text,
          start_ms: startMs,
          end_ms: endMs,
          confidence: item.confidence,
          force_pass1: Boolean(item.forcePass1),
        });
        await new Promise((r) => setTimeout(r, 380));
      }
    } finally {
      setSimulating(false);
    }
  };

  const displayMs = isRecording ? elapsedMs : durationMs;
  const barLevels = Array.from({ length: 8 }, (_, idx) => {
    const threshold = (idx + 1) / 8;
    return isRecording && !isPaused && micLevel >= threshold * 0.7;
  });

  return (
    <>
      <header className="no-print sticky top-0 z-20 border-b border-border bg-surface-elevated/85 backdrop-blur-md">
        <div className="mx-auto flex min-h-[56px] max-w-[1680px] flex-wrap items-center justify-between gap-2.5 px-3 py-2 sm:px-5">
          {/* Left: Back + Status + Recording Capsule */}
          <div className="flex flex-wrap items-center gap-2.5">
            <Link
              href="/"
              aria-label="Back to Meetings Library"
              className="inline-flex h-9 items-center gap-1.5 rounded-full px-2.5 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
            >
              <ArrowLeft className="h-4 w-4" aria-hidden="true" />
              <span className="hidden sm:inline">Meetings</span>
            </Link>

            <span className="h-4 w-px bg-border" aria-hidden="true" />

            {/* Recording Pill */}
            <div className="flex items-center gap-1.5 rounded-full border border-border bg-surface p-1">
              {!isRecording && status !== "completed" && status !== "processing" && (
                <button
                  type="button"
                  onClick={() => void start()}
                  className="inline-flex min-h-[34px] items-center gap-1.5 rounded-full bg-red-600 px-3.5 text-xs font-medium text-white hover:bg-red-700"
                >
                  <Mic className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Record</span>
                </button>
              )}

              {isRecording && !isPaused && (
                <button
                  type="button"
                  onClick={async () => {
                    await pause();
                    await sendControl({ type: "pause" });
                  }}
                  className="inline-flex min-h-[34px] items-center gap-1.5 rounded-full bg-amber-500 px-3.5 text-xs font-medium text-slate-950 hover:bg-amber-400"
                >
                  <Pause className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Pause</span>
                </button>
              )}

              {isRecording && isPaused && (
                <button
                  type="button"
                  onClick={async () => {
                    await resume();
                    await sendControl({ type: "resume" });
                  }}
                  className="inline-flex min-h-[34px] items-center gap-1.5 rounded-full bg-emerald-600 px-3.5 text-xs font-medium text-white hover:bg-emerald-700"
                >
                  <Play className="h-3.5 w-3.5" aria-hidden="true" />
                  <span>Resume</span>
                </button>
              )}

              {(isRecording || status === "recording" || status === "paused") && (
                <button
                  type="button"
                  onClick={() => void handleStopAndConsolidate()}
                  className="inline-flex min-h-[34px] items-center gap-1.5 rounded-full bg-surface-elevated px-3.5 text-xs font-medium text-ink shadow-xs hover:bg-surface-hover"
                >
                  <Square className="h-3 w-3 fill-current" aria-hidden="true" />
                  <span>Finish</span>
                </button>
              )}

              <div className="flex items-center gap-2 px-2.5 text-xs font-medium tabular-nums text-ink">
                <span
                  className={`h-2 w-2 rounded-full ${
                    isRecording && !isPaused
                      ? isSpeechActive
                        ? "animate-pulse bg-emerald-500"
                        : "bg-red-500"
                      : status === "processing"
                      ? "animate-pulse bg-accent"
                      : "bg-ink-muted/50"
                  }`}
                  aria-hidden="true"
                />
                <span>{formatOffsetMs(displayMs)}</span>

                <div
                  className="hidden sm:flex items-end gap-0.5 h-3.5"
                  aria-label="Microphone input level"
                >
                  {barLevels.map((active, i) => (
                    <span
                      key={i}
                      className={`w-0.5 rounded-full transition-colors ${
                        active ? "bg-emerald-500" : "bg-border-strong"
                      }`}
                      style={{ height: `${35 + (i % 4) * 18}%` }}
                    />
                  ))}
                </div>
              </div>
            </div>

            <span className="hidden items-center gap-1.5 text-xs text-ink-muted md:inline-flex">
              <span
                className={`h-1.5 w-1.5 rounded-full ${
                  wsConnected ? "bg-emerald-500" : "bg-amber-500"
                }`}
                aria-hidden="true"
              />
              <span>
                {wsConnected ? `Live · ${sttMode}` : "Offline"}
                {isRecording ? ` · ${vadEngine}` : ""}
              </span>
              {bufferedChunkCount > 0 && (
                <span className="rounded-full bg-accent-soft px-2 py-0.5 text-[11px] font-medium text-accent tabular-nums">
                  {bufferedChunkCount} buffered
                </span>
              )}
            </span>
          </div>

          {/* Right: Secondary Toolbar Actions */}
          <div className="flex flex-wrap items-center gap-1.5">
            {status !== "completed" && (
              <button
                type="button"
                disabled={simulating}
                onClick={() => void handleSimulateConversation()}
                title="Stream a multi-speaker test conversation"
                className="inline-flex min-h-[36px] items-center gap-1.5 rounded-full px-3 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink disabled:opacity-50"
              >
                <Radio className="h-3.5 w-3.5" aria-hidden="true" />
                <span>{simulating ? "Streaming…" : "Demo"}</span>
              </button>
            )}

            <button
              type="button"
              onClick={() => void handlePinMoment()}
              title="Pin Moment (Cmd/Ctrl + Shift + P)"
              className="inline-flex min-h-[36px] items-center gap-1.5 rounded-full px-3 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
            >
              <BookmarkPlus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Pin</span>
            </button>

            <button
              type="button"
              onClick={() => redactDialogRef.current?.showModal()}
              className="inline-flex min-h-[36px] items-center gap-1.5 rounded-full px-3 text-xs font-medium text-red-600 hover:bg-red-500/10 dark:text-red-400"
            >
              <ShieldAlert className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Redact -2m</span>
            </button>

            <button
              type="button"
              onClick={onOpenExport}
              className="inline-flex min-h-[36px] items-center gap-1.5 rounded-full bg-ink px-4 text-xs font-medium text-surface-elevated hover:opacity-90"
            >
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              <span>Export</span>
            </button>
          </div>
        </div>

        {(micError || pinToast) && (
          <div className="mx-auto flex max-w-[1680px] items-center justify-between border-t border-border px-4 py-1.5 text-xs">
            {micError ? (
              <p role="alert" className="text-red-600 dark:text-red-400">
                Microphone unavailable: {micError}
              </p>
            ) : (
              <span />
            )}
            {pinToast && (
              <span role="status" className="font-medium text-amber-600 dark:text-amber-400">
                {pinToast}
              </span>
            )}
          </div>
        )}
      </header>

      <dialog
        ref={redactDialogRef}
        {...({ closedby: "any" } as Record<string, string>)}
        aria-labelledby="redact-dialog-title"
        onClose={() => {
          if (redactDialogRef.current) {
            redactDialogRef.current.returnValue = "";
          }
        }}
        className="w-full max-w-md rounded-2xl border border-border bg-surface-elevated p-6 text-ink shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-xs"
      >
        <h1 id="redact-dialog-title" className="text-lg font-semibold tracking-[-0.01em]">
          Redact last 2 minutes?
        </h1>
        <p className="mt-2 text-sm leading-relaxed text-ink-muted">
          Permanently removes audio, transcript segments, and unedited AI notes from the last 120 seconds. Your locked manual notes stay intact.
        </p>
        <div className="mt-6 flex justify-end gap-2.5">
          <button
            type="button"
            autoFocus
            onClick={() => redactDialogRef.current?.close("cancel")}
            className="min-h-[40px] rounded-full border border-border-strong px-4 text-sm font-medium text-ink hover:bg-surface-muted"
          >
            Cancel
          </button>
          <button
            type="button"
            disabled={redacting}
            onClick={() => void handleConfirmRedact()}
            className="min-h-[40px] rounded-full bg-red-600 px-4 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-60"
          >
            {redacting ? "Redacting…" : "Redact"}
          </button>
        </div>
      </dialog>
    </>
  );
}
