"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Plus, Search, Trash2, X } from "lucide-react";
import { AppHeader } from "@/components/AppHeader";
import { ApiError, api, getStoredToken } from "@/lib/api";
import { formatOffsetMs } from "@/lib/markdown";
import type { SearchHit, SessionSummary, User } from "@/lib/types";

function statusDotClass(status: SessionSummary["status"]): string {
  switch (status) {
    case "recording":
      return "bg-red-500";
    case "paused":
      return "bg-amber-500";
    case "processing":
      return "bg-blue-500 animate-pulse";
    case "completed":
      return "bg-emerald-500";
    case "failed":
      return "bg-red-600";
    default:
      return "bg-ink-muted/50";
  }
}

export default function SessionsLibraryPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);

  const [searchQuery, setSearchQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchHits, setSearchHits] = useState<SearchHit[] | null>(null);

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      router.replace("/login");
      return;
    }

    const load = async () => {
      try {
        const [me, list] = await Promise.all([
          api.getMe(),
          api.listSessions(),
        ]);
        setUser(me);
        setSessions(list);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
          return;
        }
      } finally {
        setLoading(false);
      }
    };
    void load();
  }, [router]);

  const handleNewSession = async () => {
    if (creating) return;
    setCreating(true);
    try {
      const created = await api.createSession({ title: "Untitled Meeting" });
      router.push(`/sessions/${created.id}`);
    } finally {
      setCreating(false);
    }
  };

  const handleDeleteSession = async (id: string) => {
    setSessions((prev) => prev.filter((s) => s.id !== id));
    await api.deleteSession(id);
  };

  const handleSearch = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const q = searchQuery.trim();
    if (!q) {
      setSearchHits(null);
      return;
    }
    setSearching(true);
    try {
      const res = await api.search(q, 15);
      setSearchHits(res.hits);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="min-h-screen bg-surface">
      <AppHeader userEmail={user?.email} />

      <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6 sm:py-10">
        {/* Header Row */}
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-[-0.02em] text-ink sm:text-[28px]">
              Meetings
            </h1>
            <p className="mt-1 text-sm text-ink-muted">
              Record live discussions or search across past decisions and topics.
            </p>
          </div>

          <button
            type="button"
            disabled={creating}
            onClick={() => void handleNewSession()}
            className="inline-flex min-h-[42px] items-center gap-2 rounded-full bg-accent px-5 text-sm font-medium text-white shadow-sm hover:opacity-90 disabled:opacity-60"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            <span>{creating ? "Creating…" : "New Meeting"}</span>
          </button>
        </div>

        {/* Google-style Pill Search Bar */}
        <section aria-label="Search across meetings" className="mt-6">
          <search>
            <form
              method="GET"
              onSubmit={(e) => void handleSearch(e)}
              className="relative flex items-center"
            >
              <Search
                className="pointer-events-none absolute left-4 h-4 w-4 text-ink-muted"
                aria-hidden="true"
              />
              <label htmlFor="hybrid-search-input" className="sr-only">
                Search meetings by topic or question
              </label>
              <input
                id="hybrid-search-input"
                name="q"
                type="search"
                spellCheck={false}
                enterKeyHint="search"
                value={searchQuery}
                onChange={(e) => {
                  setSearchQuery(e.target.value);
                  if (!e.target.value.trim()) setSearchHits(null);
                }}
                placeholder='Search decisions, action items, or ask "what did we decide about auth?"'
                className="mn-field min-h-[48px] w-full rounded-full pl-11 pr-28 text-sm shadow-xs placeholder:text-ink-muted"
              />
              <div className="absolute right-2 flex items-center gap-1">
                {searchQuery && (
                  <button
                    type="button"
                    aria-label="Clear search"
                    onClick={() => {
                      setSearchQuery("");
                      setSearchHits(null);
                    }}
                    className="inline-flex h-8 w-8 items-center justify-center rounded-full text-ink-muted hover:bg-surface-muted hover:text-ink"
                  >
                    <X className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                )}
                <button
                  type="submit"
                  disabled={searching || !searchQuery.trim()}
                  className="min-h-[34px] rounded-full bg-surface-muted px-3.5 text-xs font-medium text-ink hover:bg-surface-hover disabled:opacity-40"
                >
                  {searching ? "Searching…" : "Search"}
                </button>
              </div>
            </form>
          </search>

          {searchHits !== null && (
            <div className="mt-4 rounded-2xl border border-border bg-surface-elevated p-4 shadow-xs">
              <div className="flex items-center justify-between px-1 pb-2">
                <h2 className="text-xs font-semibold uppercase tracking-wider text-ink-muted">
                  {searchHits.length}{" "}
                  {searchHits.length === 1 ? "Match" : "Matches"}
                </h2>
              </div>

              {searchHits.length === 0 ? (
                <p className="px-1 py-4 text-sm text-ink-muted">
                  No matching topics or summaries found.
                </p>
              ) : (
                <div className="space-y-2">
                  {searchHits.map((hit, i) => {
                    const href = hit.section_id
                      ? `/sessions/${hit.session_id}?section=${hit.section_id}&t=${hit.start_ms}`
                      : `/sessions/${hit.session_id}`;
                    return (
                      <article
                        key={`${hit.session_id}-${hit.section_id || i}`}
                        className="mn-card rounded-xl border border-border bg-surface p-3.5 hover:bg-surface-elevated"
                      >
                        <div className="flex items-baseline justify-between gap-3">
                          <h3 className="text-sm font-medium text-ink">
                            <Link href={href} className="card-link">
                              <span>{hit.session_title}</span>
                              {hit.section_title && (
                                <span className="text-ink-muted">
                                  {" "}
                                  / {hit.section_title}
                                </span>
                              )}
                            </Link>
                          </h3>
                          <span className="tabular-nums shrink-0 text-xs text-ink-muted">
                            {formatOffsetMs(hit.start_ms)}
                          </span>
                        </div>
                        <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-ink-muted">
                          {hit.snippet}
                        </p>
                      </article>
                    );
                  })}
                </div>
              )}
            </div>
          )}
        </section>

        {/* Meetings Grid */}
        <section aria-label="Your meetings" className="mt-8">
          {loading ? (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {[1, 2, 3].map((n) => (
                <div
                  key={n}
                  className="h-44 animate-pulse rounded-2xl border border-border bg-surface-elevated"
                />
              ))}
            </div>
          ) : sessions.length === 0 ? (
            <div className="rounded-2xl border border-border bg-surface-elevated px-6 py-14 text-center">
              <h2 className="text-base font-semibold text-ink">
                No meetings yet
              </h2>
              <p className="mx-auto mt-1.5 max-w-sm text-sm text-ink-muted">
                Start a meeting to capture live diarized notes, pin key moments, and generate clean summaries.
              </p>
              <button
                type="button"
                onClick={() => void handleNewSession()}
                className="mt-5 inline-flex min-h-[42px] items-center gap-2 rounded-full bg-accent px-5 text-sm font-medium text-white shadow-sm hover:opacity-90"
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                <span>New Meeting</span>
              </button>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {sessions.map((sess) => (
                <article
                  key={sess.id}
                  className="mn-card flex flex-col justify-between rounded-2xl border border-border bg-surface-elevated p-5"
                >
                  <header className="flex items-center justify-between gap-2 text-xs text-ink-muted">
                    <span className="inline-flex items-center gap-1.5">
                      <span
                        className={`h-2 w-2 rounded-full ${statusDotClass(sess.status)}`}
                        aria-hidden="true"
                      />
                      <span className="capitalize">{sess.status}</span>
                    </span>
                    <span className="tabular-nums">
                      {new Date(sess.created_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                      })}{" "}
                      · {formatOffsetMs(sess.duration_ms)}
                    </span>
                  </header>

                  <div className="mt-3 flex-1">
                    <h2 className="text-[15px] font-semibold leading-snug tracking-[-0.01em] text-ink">
                      <Link
                        href={`/sessions/${sess.id}`}
                        className="card-link line-clamp-2"
                      >
                        {sess.title}
                      </Link>
                    </h2>

                    {sess.executive_summary ? (
                      <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-ink-muted">
                        {sess.executive_summary}
                      </p>
                    ) : sess.agenda_preview.length > 0 ? (
                      <p className="mt-2 line-clamp-2 text-[13px] leading-relaxed text-ink-muted">
                        {sess.agenda_preview.join(" · ")}
                      </p>
                    ) : (
                      <p className="mt-2 text-[13px] text-ink-muted/70">
                        Ready to record
                      </p>
                    )}
                  </div>

                  <footer className="mt-4 flex items-center justify-between border-t border-border pt-3 text-xs text-ink-muted">
                    <span className="tabular-nums">
                      {sess.decision_count} decisions · {sess.action_item_count}{" "}
                      actions
                    </span>

                    <button
                      type="button"
                      aria-label={`Delete ${sess.title}`}
                      onClick={() => void handleDeleteSession(sess.id)}
                      className="mn-hover-actions inline-flex h-7 w-7 items-center justify-center rounded-full text-ink-muted hover:bg-red-500/12 hover:text-red-600"
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </footer>
                </article>
              ))}
            </div>
          )}
        </section>
      </main>
    </div>
  );
}
