"use client";

import { useEffect, useRef, useState } from "react";
import { Pause, Play, Volume2, X } from "lucide-react";
import { api } from "@/lib/api";
import { formatOffsetMs } from "@/lib/markdown";
import { useSessionStore } from "@/store/sessionStore";

export function AudioPlayer({ sessionId }: { sessionId: string }) {
  const activeSlice = useSessionStore((s) => s.activeAudioSlice);
  const clearAudioSlice = useSessionStore((s) => s.clearAudioSlice);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [audioSrc, setAudioSrc] = useState<string | null>(null);
  const [playing, setPlaying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!activeSlice) return;
    let cancelled = false;

    const loadAndPlay = async () => {
      setError(null);
      try {
        let src = audioSrc;
        if (!src) {
          const res = await api.getAudioUrl(sessionId);
          src = res.url || res.stream_url;
          if (!cancelled) setAudioSrc(src);
        }
        const el = audioRef.current;
        if (!el || !src) return;
        if (el.src !== src) el.src = src;
        el.currentTime = activeSlice.startMs / 1000;
        await el.play();
        if (!cancelled) setPlaying(true);
      } catch {
        if (!cancelled) {
          setError("Could not play audio clip.");
          setPlaying(false);
        }
      }
    };

    void loadAndPlay();
    return () => {
      cancelled = true;
    };
  }, [activeSlice, audioSrc, sessionId]);

  if (!activeSlice) return null;

  return (
    <div className="no-print fixed bottom-5 right-5 z-30 flex items-center gap-3 rounded-full border border-border bg-surface-elevated/90 px-4 py-2 shadow-xl backdrop-blur-md">
      <Volume2 className="h-4 w-4 text-accent" aria-hidden="true" />
      <div className="text-xs">
        <div className="font-medium tabular-nums text-ink">
          {formatOffsetMs(activeSlice.startMs)} – {formatOffsetMs(activeSlice.endMs)}
        </div>
        {error && <div className="text-red-500">{error}</div>}
      </div>

      <button
        type="button"
        aria-label={playing ? "Pause audio clip" : "Play audio clip"}
        onClick={() => {
          const el = audioRef.current;
          if (!el) return;
          if (playing) {
            el.pause();
            setPlaying(false);
          } else {
            void el.play();
            setPlaying(true);
          }
        }}
        className="inline-flex h-8 w-8 items-center justify-center rounded-full bg-accent text-white"
      >
        {playing ? (
          <Pause className="h-3.5 w-3.5" aria-hidden="true" />
        ) : (
          <Play className="h-3.5 w-3.5" aria-hidden="true" />
        )}
      </button>

      <button
        type="button"
        aria-label="Close audio player"
        onClick={() => {
          audioRef.current?.pause();
          setPlaying(false);
          clearAudioSlice();
        }}
        className="inline-flex h-8 w-8 items-center justify-center rounded-full text-ink-muted hover:bg-surface-muted hover:text-ink"
      >
        <X className="h-4 w-4" aria-hidden="true" />
      </button>

      <audio
        ref={audioRef}
        onTimeUpdate={(e) => {
          const el = e.currentTarget;
          if (el.currentTime * 1000 >= activeSlice.endMs) {
            el.pause();
            setPlaying(false);
          }
        }}
        onEnded={() => setPlaying(false)}
      />
    </div>
  );
}
