"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, Check, Plus, Trash2 } from "lucide-react";
import { AppHeader } from "@/components/AppHeader";
import { ApiError, api, getStoredToken } from "@/lib/api";
import type { AudioRetentionPolicy, GlossaryTerm, User } from "@/lib/types";

const RETENTION_OPTIONS: Array<{
  value: AudioRetentionPolicy;
  label: string;
  description: string;
}> = [
  {
    value: "delete_after_processing",
    label: "Delete immediately after consolidation",
    description:
      "Highest privacy: audio WAV is purged as soon as final notes finish generating.",
  },
  {
    value: "retain_7d",
    label: "Retain audio for 7 days",
    description:
      "Recommended. Enables [↗] audio playback citations for 7 days before automatic purge.",
  },
  {
    value: "retain_30d",
    label: "Retain audio for 30 days",
    description:
      "Keeps meeting audio clips accessible for 30 days before scheduled deletion.",
  },
];

export default function SettingsPage() {
  const router = useRouter();
  const [user, setUser] = useState<User | null>(null);
  const [retention, setRetention] = useState<AudioRetentionPolicy>("retain_7d");
  const [savingRetention, setSavingRetention] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);

  const [terms, setTerms] = useState<GlossaryTerm[]>([]);
  const [newTerm, setNewTerm] = useState("");
  const [boost, setBoost] = useState("1.8");
  const [addingTerm, setAddingTerm] = useState(false);

  useEffect(() => {
    const token = getStoredToken();
    if (!token) {
      router.replace("/login");
      return;
    }

    const load = async () => {
      try {
        const [me, glossary] = await Promise.all([
          api.getMe(),
          api.listGlossary(),
        ]);
        setUser(me);
        setRetention(me.default_audio_retention);
        setTerms(glossary);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        }
      }
    };
    void load();
  }, [router]);

  const handleSaveRetention = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setSavingRetention(true);
    setSavedNotice(false);
    try {
      const updated = await api.updateMe({
        default_audio_retention: retention,
      });
      setUser(updated);
      setSavedNotice(true);
      setTimeout(() => setSavedNotice(false), 2000);
    } finally {
      setSavingRetention(false);
    }
  };

  const handleAddTerm = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const cleaned = newTerm.trim();
    if (!cleaned || addingTerm) return;
    setAddingTerm(true);
    try {
      const created = await api.addGlossaryTerm(
        cleaned,
        Math.min(5, Math.max(0.5, Number(boost) || 1.5))
      );
      setTerms((prev) => [
        created,
        ...prev.filter((t) => t.id !== created.id),
      ]);
      setNewTerm("");
    } finally {
      setAddingTerm(false);
    }
  };

  const handleDeleteTerm = async (id: string) => {
    setTerms((prev) => prev.filter((t) => t.id !== id));
    await api.deleteGlossaryTerm(id);
  };

  return (
    <div className="min-h-screen bg-surface">
      <AppHeader userEmail={user?.email} />

      <main className="mx-auto max-w-3xl space-y-8 px-4 py-8 sm:px-6">
        <div>
          <Link
            href="/"
            className="inline-flex items-center gap-1.5 text-xs font-medium text-ink-muted hover:text-ink"
          >
            <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Back to Meetings</span>
          </Link>
          <h1 className="mt-2.5 text-2xl font-semibold tracking-[-0.02em] text-ink">
            Settings
          </h1>
          <p className="mt-1 text-sm text-ink-muted">
            Privacy retention policies and speech recognition vocabulary boosting.
          </p>
        </div>

        {/* Audio Retention Defaults */}
        <section className="rounded-2xl border border-border bg-surface-elevated p-6">
          <h2 className="text-base font-semibold tracking-[-0.01em] text-ink">
            Default Audio Retention
          </h2>

          <form
            onSubmit={(e) => void handleSaveRetention(e)}
            className="mt-4 space-y-4"
          >
            <fieldset className="space-y-2.5">
              <legend className="text-xs text-ink-muted">
                Choose how recorded audio is handled after Pass 2 consolidation
              </legend>

              {RETENTION_OPTIONS.map((opt) => (
                <label
                  key={opt.value}
                  htmlFor={`retention-${opt.value}`}
                  className="mn-choice-card flex cursor-pointer items-start gap-3 rounded-xl bg-surface p-3.5"
                >
                  <input
                    id={`retention-${opt.value}`}
                    type="radio"
                    name="audio_retention"
                    value={opt.value}
                    checked={retention === opt.value}
                    onChange={() => setRetention(opt.value)}
                    className="mt-1 h-4 w-4 shrink-0"
                  />
                  <div>
                    <div className="text-sm font-medium text-ink">
                      {opt.label}
                    </div>
                    <p className="mt-0.5 text-xs leading-relaxed text-ink-muted">
                      {opt.description}
                    </p>
                  </div>
                </label>
              ))}
            </fieldset>

            <div className="flex items-center gap-3 pt-1">
              <button
                type="submit"
                disabled={savingRetention}
                className="inline-flex min-h-[40px] items-center rounded-full bg-accent px-5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
              >
                {savingRetention ? "Saving…" : "Save Default"}
              </button>
              {savedNotice && (
                <span
                  role="status"
                  className="inline-flex items-center gap-1 text-xs font-medium text-emerald-600 dark:text-emerald-400"
                >
                  <Check className="h-3.5 w-3.5" aria-hidden="true" />
                  Saved
                </span>
              )}
            </div>
          </form>
        </section>

        {/* Custom Glossary */}
        <section className="rounded-2xl border border-border bg-surface-elevated p-6">
          <h2 className="text-base font-semibold tracking-[-0.01em] text-ink">
            Custom STT Glossary
          </h2>
          <p className="mt-1 text-xs text-ink-muted">
            Keyterms injected into Deepgram Nova-3 to boost recognition of product names and acronyms.
          </p>

          <form
            onSubmit={(e) => void handleAddTerm(e)}
            className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-end"
          >
            <div className="flex flex-1 flex-col gap-1">
              <label
                htmlFor="glossary-term"
                className="text-xs font-medium text-ink"
              >
                Term <span aria-hidden="true">*</span>
              </label>
              <input
                id="glossary-term"
                name="term"
                type="text"
                required
                value={newTerm}
                onChange={(e) => setNewTerm(e.target.value)}
                placeholder="e.g., pgvector"
                className="mn-field min-h-[42px] rounded-xl px-3.5 text-sm"
              />
            </div>

            <div className="flex w-full flex-col gap-1 sm:w-28">
              <label
                htmlFor="glossary-boost"
                className="text-xs font-medium text-ink"
              >
                Boost (0.5–5)
              </label>
              <input
                id="glossary-boost"
                name="boost"
                type="number"
                step="0.5"
                min="0.5"
                max="5"
                value={boost}
                onChange={(e) => setBoost(e.target.value)}
                className="mn-field min-h-[42px] rounded-xl px-3 text-sm tabular-nums"
              />
            </div>

            <button
              type="submit"
              disabled={addingTerm}
              className="inline-flex min-h-[42px] items-center justify-center gap-1.5 rounded-full bg-accent px-5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-60"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              <span>Add</span>
            </button>
          </form>

          <ul className="mt-5 divide-y divide-border">
            {terms.length === 0 ? (
              <li className="py-4 text-xs text-ink-muted">
                No custom keyterms added yet.
              </li>
            ) : (
              terms.map((t) => (
                <li
                  key={t.id}
                  className="mn-row flex items-center justify-between gap-3 py-2.5 text-sm"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="font-medium text-ink">{t.term}</span>
                    <span className="rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-medium text-ink-muted tabular-nums">
                      ×{t.boost.toFixed(1)}
                    </span>
                  </div>
                  <button
                    type="button"
                    aria-label={`Remove ${t.term}`}
                    onClick={() => void handleDeleteTerm(t.id)}
                    className="mn-hover-actions inline-flex h-7 w-7 items-center justify-center rounded-full text-ink-muted hover:bg-red-500/12 hover:text-red-600"
                  >
                    <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </li>
              ))
            )}
          </ul>
        </section>
      </main>
    </div>
  );
}
