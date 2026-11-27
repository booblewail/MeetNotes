"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Search, Volume2 } from "lucide-react";
import { api } from "@/lib/api";
import { formatOffsetMs } from "@/lib/markdown";
import type { Speaker, TranscriptSegment } from "@/lib/types";
import { useSessionStore } from "@/store/sessionStore";

const LOW_CONFIDENCE_THRESHOLD = 0.75;

export function TranscriptDrawer({ sessionId }: { sessionId: string }) {
  const speakers = useSessionStore((s) => s.speakers);
  const segments = useSessionStore((s) => s.segments);
  const interimSegment = useSessionStore((s) => s.interimSegment);
  const highlightedSegmentIds = useSessionStore((s) => s.highlightedSegmentIds);
  const audioAvailable = useSessionStore((s) => s.audioAvailable);

  const applySpeakerLocal = useSessionStore((s) => s.applySpeakerLocal);
  const playAudioAtMs = useSessionStore((s) => s.playAudioAtMs);

  const [query, setQuery] = useState("");
  const [renamingSpeakerId, setRenamingSpeakerId] = useState<string | null>(null);
  const [speakerDraft, setSpeakerDraft] = useState("");

  const segmentRefs = useRef<Record<string, HTMLLIElement | null>>({});

  useEffect(() => {
    if (highlightedSegmentIds.length === 0) return;
    const firstId = highlightedSegmentIds[0];
    segmentRefs.current[firstId]?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  }, [highlightedSegmentIds]);

  const handleRenameSpeaker = async (sp: Speaker) => {
    setRenamingSpeakerId(null);
    const trimmed = speakerDraft.trim();
    if (!trimmed || trimmed === sp.display_name) return;
    const updated = await api.renameSpeaker(sessionId, sp.id, trimmed);
    applySpeakerLocal(updated);
  };

  const normalizedQuery = query.trim().toLowerCase();
  const visibleSegments = normalizedQuery
    ? segments.filter(
        (seg) =>
          seg.text.toLowerCase().includes(normalizedQuery) ||
          seg.speaker_label.toLowerCase().includes(normalizedQuery)
      )
    : segments;

  const renderSegmentText = (seg: TranscriptSegment) => {
    if (!seg.words || seg.words.length === 0) {
      return (
        <span className={seg.has_low_confidence ? "low-confidence-token" : ""}>
          {seg.text}
        </span>
      );
    }
    return seg.words.map((w, idx) => {
      const low = w.confidence < LOW_CONFIDENCE_THRESHOLD;
      return (
        <span
          key={`${seg.id}-w-${idx}`}
          title={
            low
              ? `Low confidence (${Math.round(w.confidence * 100)}%)`
              : undefined
          }
          className={low ? "low-confidence-token" : undefined}
        >
          {w.word}
          {idx < seg.words.length - 1 ? " " : ""}
        </span>
      );
    });
  };

  return (
    <aside
      aria-label="Live Diarized Transcript"
      className="flex h-full flex-col gap-3 bg-surface p-4 sm:p-5"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
          Transcript
        </h2>
        <span className="tabular-nums text-[11px] text-ink-muted">
          {segments.length} turns
        </span>
      </div>

      {/* Speaker Rename Chips */}
      {speakers.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {speakers.map((sp) =>
            renamingSpeakerId === sp.id ? (
              <form
                key={sp.id}
                onSubmit={(e) => {
                  e.preventDefault();
                  void handleRenameSpeaker(sp);
                }}
                className="inline-flex items-center gap-1"
              >
                <input
                  type="text"
                  value={speakerDraft}
                  onChange={(e) => setSpeakerDraft(e.target.value)}
                  onBlur={() => void handleRenameSpeaker(sp)}
                  autoFocus
                  className="mn-field w-28 rounded-full px-2.5 py-0.5 text-xs"
                />
                <button
                  type="submit"
                  aria-label="Confirm speaker name"
                  className="inline-flex h-6 w-6 items-center justify-center rounded-full bg-accent text-white"
                >
                  <Check className="h-3 w-3" aria-hidden="true" />
                </button>
              </form>
            ) : (
              <button
                key={sp.id}
                type="button"
                title="Click to rename speaker"
                onClick={() => {
                  setRenamingSpeakerId(sp.id);
                  setSpeakerDraft(sp.display_name);
                }}
                className="inline-flex items-center gap-1.5 rounded-full bg-surface-elevated px-2.5 py-1 text-xs font-medium text-ink shadow-xs hover:bg-surface-hover"
              >
                <span
                  className="h-2 w-2 rounded-full"
                  style={{ backgroundColor: sp.color_hex }}
                  aria-hidden="true"
                />
                <span>{sp.display_name}</span>
              </button>
            )
          )}
        </div>
      )}

      {/* Pill Filter Input */}
      <search>
        <form
          onSubmit={(e) => e.preventDefault()}
          className="relative flex items-center"
        >
          <Search
            className="pointer-events-none absolute left-3 h-3.5 w-3.5 text-ink-muted"
            aria-hidden="true"
          />
          <label htmlFor="transcript-search" className="sr-only">
            Filter transcript
          </label>
          <input
            id="transcript-search"
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            spellCheck={false}
            placeholder="Filter transcript…"
            className="mn-field min-h-[36px] w-full rounded-full pl-8 pr-3 text-xs placeholder:text-ink-muted"
          />
        </form>
      </search>

      {/* Conversational Feed */}
      <div className="min-h-0 flex-1 overflow-y-auto pr-1">
        {visibleSegments.length === 0 && !interimSegment ? (
          <p className="py-10 text-center text-xs text-ink-muted">
            {query ? "No matching utterances." : "Waiting for speech…"}
          </p>
        ) : (
          <ul className="divide-y divide-border/70">
            {visibleSegments.map((seg) => {
              const isHighlighted = highlightedSegmentIds.includes(seg.id);
              return (
                <li
                  key={seg.id}
                  ref={(el) => {
                    segmentRefs.current[seg.id] = el;
                  }}
                  className={`py-3 transition-colors ${
                    isHighlighted
                      ? "rounded-xl bg-accent-soft px-3 border-l-2 border-accent"
                      : "px-1"
                  }`}
                >
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span className="inline-flex items-center gap-1.5 font-semibold text-ink">
                      <span
                        className="h-2 w-2 rounded-full"
                        style={{ backgroundColor: seg.speaker_color }}
                        aria-hidden="true"
                      />
                      <span>{seg.speaker_label}</span>
                    </span>

                    <button
                      type="button"
                      disabled={!audioAvailable}
                      onClick={() => playAudioAtMs(seg.start_ms)}
                      title={
                        audioAvailable
                          ? "Play 10s audio clip around this turn"
                          : "Timestamp"
                      }
                      className="inline-flex items-center gap-1 text-[11px] tabular-nums text-ink-muted hover:text-accent disabled:cursor-default disabled:hover:text-ink-muted"
                    >
                      {audioAvailable && (
                        <Volume2 className="h-3 w-3" aria-hidden="true" />
                      )}
                      <span>{formatOffsetMs(seg.start_ms)}</span>
                    </button>
                  </div>

                  <p className="mt-1 text-[13.5px] leading-relaxed text-ink">
                    {renderSegmentText(seg)}
                  </p>
                </li>
              );
            })}
          </ul>
        )}

        {interimSegment && (
          <div className="mt-2 rounded-xl bg-surface-elevated/80 p-3 text-[13.5px] italic text-ink-muted">
            <div className="mb-1 flex items-center justify-between text-xs not-italic">
              <span className="font-medium text-ink">
                {interimSegment.speaker_label}
              </span>
              <span className="tabular-nums text-[11px]">
                {formatOffsetMs(interimSegment.start_ms)}
              </span>
            </div>
            {interimSegment.text}
          </div>
        )}
      </div>
    </aside>
  );
}
