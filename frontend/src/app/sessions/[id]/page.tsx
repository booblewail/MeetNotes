"use client";

import { Suspense, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { ExportModal } from "@/components/export/ExportModal";
import { AudioPlayer } from "@/components/recorder/AudioPlayer";
import { RecordingBar } from "@/components/recorder/RecordingBar";
import { AgendaSidebar } from "@/components/workspace/AgendaSidebar";
import { NotesDocument } from "@/components/workspace/NotesDocument";
import { TranscriptDrawer } from "@/components/workspace/TranscriptDrawer";
import { useSessionSocket } from "@/hooks/useSessionSocket";
import { ApiError, api, getStoredToken } from "@/lib/api";
import { useSessionStore } from "@/store/sessionStore";

type MobilePane = "notes" | "agenda" | "transcript";

function WorkspaceInner() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  const router = useRouter();
  const sessionId = params.id;

  const hydrateSession = useSessionStore((s) => s.hydrateSession);
  const reset = useSessionStore((s) => s.reset);
  const setActiveSectionId = useSessionStore((s) => s.setActiveSectionId);

  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [mobilePane, setMobilePane] = useState<MobilePane>("notes");
  const tabRefs = useRef<Record<MobilePane, HTMLButtonElement | null>>({
    notes: null,
    agenda: null,
    transcript: null,
  });

  const { sendAudioChunk, sendControl } = useSessionSocket(
    sessionId,
    !loading && !loadError
  );

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      router.replace("/login");
      return;
    }

    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setLoadError(null);
      try {
        const detail = await api.getSession(sessionId);
        if (cancelled) return;
        hydrateSession(detail);

        const targetSection = searchParams.get("section");
        if (targetSection) {
          setActiveSectionId(targetSection);
          requestAnimationFrame(() => {
            document
              .getElementById(`section-${targetSection}`)
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          });
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
          return;
        }
        if (!cancelled) {
          setLoadError(
            err instanceof Error ? err.message : "Failed to load session"
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
      reset();
    };
  }, [sessionId, hydrateSession, reset, router, searchParams, setActiveSectionId]);

  const selectMobilePane = (pane: MobilePane) => {
    setMobilePane(pane);
    tabRefs.current[pane]?.scrollIntoView({
      block: "nearest",
      inline: "nearest",
    });
  };

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center bg-surface text-sm text-ink-muted">
        Loading workspace…
      </main>
    );
  }

  if (loadError) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center gap-4 bg-surface px-4 text-center">
        <p role="alert" className="text-sm text-red-600 dark:text-red-400">
          {loadError}
        </p>
        <Link
          href="/"
          className="inline-flex items-center gap-1.5 rounded-full bg-accent px-5 py-2 text-sm font-medium text-white"
        >
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
          <span>Back to Meetings</span>
        </Link>
      </main>
    );
  }

  return (
    <div className="flex min-h-screen flex-col bg-surface">
      {/* Unified Frosted-Glass Recording & Workspace Header */}
      <RecordingBar
        sessionId={sessionId}
        sendAudioChunk={sendAudioChunk}
        sendControl={sendControl}
        onOpenExport={() => setExportOpen(true)}
      />

      {/* Mobile Tab Bar (< lg) */}
      <div className="no-print border-b border-border bg-surface-elevated px-3 lg:hidden">
        <div
          role="tablist"
          aria-label="Meeting Workspace Panes"
          className="flex overflow-x-auto"
        >
          {(
            [
              { id: "notes", label: "Notes" },
              { id: "agenda", label: "Outline" },
              { id: "transcript", label: "Transcript" },
            ] as const
          ).map((tab) => (
            <button
              key={tab.id}
              ref={(el) => {
                tabRefs.current[tab.id] = el;
              }}
              type="button"
              role="tab"
              id={`tab-${tab.id}`}
              aria-selected={mobilePane === tab.id}
              onClick={() => selectMobilePane(tab.id)}
              onKeyDown={(e) => {
                const order: MobilePane[] = ["notes", "agenda", "transcript"];
                const idx = order.indexOf(tab.id);
                if (e.key === "ArrowRight") {
                  const next = order[(idx + 1) % order.length];
                  selectMobilePane(next);
                  tabRefs.current[next]?.focus();
                } else if (e.key === "ArrowLeft") {
                  const prev = order[(idx - 1 + order.length) % order.length];
                  selectMobilePane(prev);
                  tabRefs.current[prev]?.focus();
                }
              }}
              className="flex-1 shrink-0 min-h-[44px] px-4 text-sm font-medium text-ink-muted"
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Hairline-Divided 3-Pane Workspace */}
      <main className="mx-auto grid w-full max-w-[1680px] flex-1 grid-cols-1 lg:h-[calc(100vh-57px)] lg:grid-cols-[248px_minmax(0,1fr)_360px]">
        <div
          role="tabpanel"
          aria-labelledby="tab-agenda"
          className={`min-h-0 overflow-hidden lg:border-r lg:border-border ${
            mobilePane === "agenda" ? "block" : "hidden lg:block"
          }`}
        >
          <AgendaSidebar onSelectSection={() => setMobilePane("notes")} />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="tab-notes"
          className={`min-h-0 overflow-hidden ${
            mobilePane === "notes" ? "block" : "hidden lg:block"
          }`}
        >
          <NotesDocument
            sessionId={sessionId}
            onJumpToCitation={() => {
              if (window.innerWidth < 1024) {
                setMobilePane("transcript");
              }
            }}
          />
        </div>

        <div
          role="tabpanel"
          aria-labelledby="tab-transcript"
          className={`min-h-0 overflow-hidden lg:border-l lg:border-border ${
            mobilePane === "transcript" ? "block" : "hidden lg:block"
          }`}
        >
          <TranscriptDrawer sessionId={sessionId} />
        </div>
      </main>

      <AudioPlayer sessionId={sessionId} />

      <ExportModal
        sessionId={sessionId}
        open={exportOpen}
        onClose={() => setExportOpen(false)}
      />
    </div>
  );
}

export default function SessionWorkspacePage() {
  return (
    <Suspense
      fallback={
        <main className="flex min-h-screen items-center justify-center bg-surface text-sm text-ink-muted">
          Loading workspace…
        </main>
      }
    >
      <WorkspaceInner />
    </Suspense>
  );
}
