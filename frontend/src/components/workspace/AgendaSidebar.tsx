"use client";

import { useEffect } from "react";
import { formatOffsetMs } from "@/lib/markdown";
import type { FilterKind } from "@/lib/types";
import { useSessionStore } from "@/store/sessionStore";

interface AgendaSidebarProps {
  onSelectSection?: (sectionId: string) => void;
}

const FILTER_OPTIONS: Array<{ id: FilterKind; label: string }> = [
  { id: "all", label: "All" },
  { id: "decision", label: "Decisions" },
  { id: "action_item", label: "Actions" },
  { id: "open_question", label: "Questions" },
  { id: "locked", label: "Locked" },
];

export function AgendaSidebar({ onSelectSection }: AgendaSidebarProps) {
  const sections = useSessionStore((s) => s.sections);
  const blocks = useSessionStore((s) => s.blocks);
  const activeFilter = useSessionStore((s) => s.activeFilter);
  const activeSectionId = useSessionStore((s) => s.activeSectionId);
  const setActiveFilter = useSessionStore((s) => s.setActiveFilter);
  const setActiveSectionId = useSessionStore((s) => s.setActiveSectionId);

  useEffect(() => {
    if (sections.length === 0) return;
    const elements = sections
      .map((s) => document.getElementById(`section-${s.id}`))
      .filter((el): el is HTMLElement => el !== null);
    if (elements.length === 0) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible.length > 0) {
          const secId = visible[0].target.getAttribute("data-section-id");
          if (secId) setActiveSectionId(secId);
        }
      },
      { rootMargin: "-15% 0px -65% 0px", threshold: 0.1 }
    );

    for (const el of elements) observer.observe(el);
    return () => observer.disconnect();
  }, [sections, setActiveSectionId]);

  const countByType = {
    decision: blocks.filter((b) => b.block_type === "decision").length,
    action_item: blocks.filter((b) => b.block_type === "action_item").length,
    open_question: blocks.filter((b) => b.block_type === "open_question").length,
    locked: blocks.filter((b) => b.is_user_edited).length,
  };

  const jumpToSection = (sectionId: string) => {
    setActiveSectionId(sectionId);
    onSelectSection?.(sectionId);
    requestAnimationFrame(() => {
      document
        .getElementById(`section-${sectionId}`)
        ?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  };

  return (
    <aside
      aria-label="Meeting Agenda Outline"
      className="flex h-full flex-col gap-6 bg-surface p-4 sm:p-5"
    >
      <div>
        <h2 className="px-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
          View
        </h2>
        <div className="mt-2 flex flex-wrap gap-1.5">
          {FILTER_OPTIONS.map((opt) => {
            const count =
              opt.id === "all"
                ? blocks.length
                : countByType[opt.id as keyof typeof countByType];
            const selected = activeFilter === opt.id;
            return (
              <button
                key={opt.id}
                type="button"
                aria-pressed={selected}
                onClick={() => setActiveFilter(opt.id)}
                className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${
                  selected
                    ? "bg-ink text-surface-elevated"
                    : "bg-surface-muted text-ink-muted hover:bg-surface-hover hover:text-ink"
                }`}
              >
                <span>{opt.label}</span>
                <span className="tabular-nums opacity-70">{count}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <h2 className="px-2 text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
          Outline
        </h2>
        {sections.length === 0 ? (
          <p className="mt-2.5 px-2 text-xs leading-relaxed text-ink-muted">
            Topic sections appear here automatically as the conversation progresses.
          </p>
        ) : (
          <nav aria-label="Detected agenda sections" className="mt-2 space-y-0.5">
            {sections.map((sec) => {
              const isActive = activeSectionId === sec.id;
              return (
                <button
                  key={sec.id}
                  type="button"
                  onClick={() => jumpToSection(sec.id)}
                  className={`flex w-full items-baseline justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-[13px] ${
                    isActive
                      ? "bg-accent-soft font-medium text-accent"
                      : "text-ink-muted hover:bg-surface-muted hover:text-ink"
                  }`}
                >
                  <span className="line-clamp-2 leading-snug">{sec.title}</span>
                  <span className="tabular-nums shrink-0 text-[11px] opacity-75">
                    {formatOffsetMs(sec.start_ms)}
                  </span>
                </button>
              );
            })}
          </nav>
        )}
      </div>
    </aside>
  );
}
