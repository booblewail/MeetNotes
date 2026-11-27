"use client";

import { useState } from "react";
import {
  ArrowUpRight,
  Bookmark,
  Edit3,
  Lock,
  Plus,
  Trash2,
} from "lucide-react";
import { FormattedText } from "@/components/workspace/FormattedText";
import { RichBlockEditor } from "@/components/workspace/RichBlockEditor";
import { api } from "@/lib/api";
import { formatOffsetMs } from "@/lib/markdown";
import type { AgendaSection, BlockType, NoteBlock } from "@/lib/types";
import { useSessionStore } from "@/store/sessionStore";

interface NotesDocumentProps {
  sessionId: string;
  onJumpToCitation?: () => void;
}

const BLOCK_TYPE_LABEL: Record<BlockType, string | null> = {
  bullet: null,
  decision: "Decision",
  action_item: "Action",
  open_question: "Question",
  scratchpad: "Note",
};

export function NotesDocument({
  sessionId,
  onJumpToCitation,
}: NotesDocumentProps) {
  const title = useSessionStore((s) => s.title);
  const status = useSessionStore((s) => s.status);
  const executiveSummary = useSessionStore((s) => s.executiveSummary);
  const pinnedMoments = useSessionStore((s) => s.pinnedMoments);
  const sections = useSessionStore((s) => s.sections);
  const blocks = useSessionStore((s) => s.blocks);
  const segments = useSessionStore((s) => s.segments);
  const activeFilter = useSessionStore((s) => s.activeFilter);
  const audioAvailable = useSessionStore((s) => s.audioAvailable);

  const setTitle = useSessionStore((s) => s.setTitle);
  const setExecutiveSummary = useSessionStore((s) => s.setExecutiveSummary);
  const applyBlockLocal = useSessionStore((s) => s.applyBlockLocal);
  const removeBlockLocal = useSessionStore((s) => s.removeBlockLocal);
  const applySectionLocal = useSessionStore((s) => s.applySectionLocal);
  const highlightSegments = useSessionStore((s) => s.highlightSegments);
  const playAudioAtMs = useSessionStore((s) => s.playAudioAtMs);

  const [editingTitle, setEditingTitle] = useState(false);
  const [titleDraft, setTitleDraft] = useState(title);
  const [editingSummary, setEditingSummary] = useState(false);
  const [editingSectionId, setEditingSectionId] = useState<string | null>(null);
  const [sectionDraft, setSectionDraft] = useState("");
  const [editingBlockId, setEditingBlockId] = useState<string | null>(null);
  const [addingInSectionId, setAddingInSectionId] = useState<string | null>(null);

  const segmentTimeMap = new Map(segments.map((seg) => [seg.id, seg.start_ms]));

  const saveTitle = async () => {
    setEditingTitle(false);
    const trimmed = titleDraft.trim();
    if (!trimmed || trimmed === title) return;
    setTitle(trimmed);
    await api.updateSession(sessionId, { title: trimmed });
  };

  const saveSummary = async (summaryText: string) => {
    setEditingSummary(false);
    const trimmed = summaryText.trim();
    if (!trimmed || trimmed === (executiveSummary || "").trim()) return;
    setExecutiveSummary(trimmed);
    await api.updateSession(sessionId, { executive_summary: trimmed });
  };

  const saveSectionTitle = async (sec: AgendaSection) => {
    setEditingSectionId(null);
    const trimmed = sectionDraft.trim();
    if (!trimmed || trimmed === sec.title) return;
    const updated = await api.updateSection(sec.id, trimmed);
    applySectionLocal(updated);
  };

  const toggleActionCompleted = async (block: NoteBlock) => {
    const nextCompleted = !block.completed;
    applyBlockLocal({
      ...block,
      completed: nextCompleted,
      is_user_edited: true,
    });
    const updated = await api.updateBlock(block.id, {
      completed: nextCompleted,
    });
    applyBlockLocal(updated);
  };

  const handleDeleteBlock = async (blockId: string) => {
    removeBlockLocal(blockId);
    await api.deleteBlock(blockId);
  };

  const matchesFilter = (b: NoteBlock) => {
    if (activeFilter === "all") return true;
    if (activeFilter === "locked") return b.is_user_edited;
    return b.block_type === activeFilter;
  };

  const citeFirstOffset = (segIds: string[]): string | null => {
    for (const sid of segIds) {
      const ms = segmentTimeMap.get(sid);
      if (ms !== undefined) return formatOffsetMs(ms);
    }
    return null;
  };

  const renderBlock = (block: NoteBlock) => {
    const isEditing = editingBlockId === block.id;
    const citeLabel = citeFirstOffset(block.source_segment_ids);
    const typeTag = BLOCK_TYPE_LABEL[block.block_type];

    if (isEditing) {
      return (
        <li key={block.id} className="py-1">
          <RichBlockEditor
            autoFocus
            initialContent={block.content}
            initialBlockType={block.block_type}
            initialSectionId={block.section_id || ""}
            initialAssignee={block.assignee || ""}
            initialDueDate={block.due_date_text || ""}
            sections={sections}
            showTypePicker
            showSectionPicker={sections.length > 0}
            submitLabel="Save & Lock"
            onCancel={() => setEditingBlockId(null)}
            onSubmit={async ({
              content,
              blockType,
              sectionId,
              assignee,
              dueDateText,
            }) => {
              const updated = await api.updateBlock(block.id, {
                content,
                block_type: blockType,
                section_id: sectionId,
                assignee: assignee || null,
                due_date_text: dueDateText || null,
              });
              applyBlockLocal(updated);
              setEditingBlockId(null);
            }}
          />
        </li>
      );
    }

    return (
      <li
        key={block.id}
        className={`mn-row group relative rounded-xl px-3 py-2 transition-colors hover:bg-surface/80 ${
          block.is_user_edited ? "border-l-2 border-amber-500/80 pl-3.5" : ""
        }`}
      >
        <div className="flex items-start gap-3">
          {block.block_type === "action_item" ? (
            <input
              type="checkbox"
              checked={block.completed}
              aria-label={`Mark action item completed: ${block.content}`}
              onChange={() => void toggleActionCompleted(block)}
              className="mt-1 h-4 w-4 shrink-0 cursor-pointer"
            />
          ) : (
            <span
              className={`mt-2 h-1.5 w-1.5 shrink-0 rounded-full ${
                block.block_type === "decision"
                  ? "bg-emerald-500"
                  : block.block_type === "open_question"
                  ? "bg-amber-500"
                  : "bg-ink-muted/50"
              }`}
              aria-hidden="true"
            />
          )}

          <div className="min-w-0 flex-1">
            <div className="text-[14.5px] leading-relaxed">
              {typeTag && (
                <span
                  className={`mr-2 inline-block text-[11px] font-semibold uppercase tracking-wide ${
                    block.block_type === "decision"
                      ? "text-emerald-600 dark:text-emerald-400"
                      : block.block_type === "action_item"
                      ? "text-accent"
                      : block.block_type === "open_question"
                      ? "text-amber-600 dark:text-amber-400"
                      : "text-ink-muted"
                  }`}
                >
                  {typeTag}
                </span>
              )}

              <span
                onClick={() => setEditingBlockId(block.id)}
                title={
                  block.has_low_confidence
                    ? "Contains low-confidence transcript words (<0.75). Click to edit and format."
                    : "Click to edit inline and format"
                }
                className={`cursor-text ${
                  block.completed ? "line-through text-ink-muted" : "text-ink"
                } ${block.has_low_confidence ? "low-confidence-token" : ""}`}
              >
                <FormattedText text={block.content} />
              </span>

              {(block.assignee || block.due_date_text) && (
                <span className="ml-2 text-xs text-ink-muted">
                  {block.assignee ? `@${block.assignee}` : ""}
                  {block.assignee && block.due_date_text ? " · " : ""}
                  {block.due_date_text ? `due ${block.due_date_text}` : ""}
                </span>
              )}

              {block.is_user_edited && (
                <span
                  title="Locked manual edit — preserved verbatim during Pass 2 consolidation"
                  className="ml-2 inline-flex items-center gap-0.5 text-[11px] font-medium text-amber-600 dark:text-amber-400"
                >
                  <Lock className="h-2.5 w-2.5" aria-hidden="true" />
                  Locked
                </span>
              )}
            </div>
          </div>

          <div className="flex shrink-0 items-center gap-1">
            {block.source_segment_ids.length > 0 && (
              <button
                type="button"
                title="Jump to quote in transcript (and play audio if retained)"
                onClick={() => {
                  highlightSegments(block.source_segment_ids, true);
                  onJumpToCitation?.();
                }}
                className="inline-flex items-center gap-0.5 rounded-full bg-surface-muted px-2 py-0.5 text-[11px] font-medium tabular-nums text-ink-muted hover:bg-accent-soft hover:text-accent"
              >
                <span>{citeLabel ?? "Cite"}</span>
                <ArrowUpRight className="h-3 w-3" aria-hidden="true" />
              </button>
            )}

            <button
              type="button"
              aria-label="Edit note block"
              onClick={() => setEditingBlockId(block.id)}
              className="no-print mn-hover-actions inline-flex h-7 w-7 items-center justify-center rounded-full text-ink-muted hover:bg-surface-muted hover:text-ink"
            >
              <Edit3 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>

            <button
              type="button"
              aria-label="Delete note block"
              onClick={() => void handleDeleteBlock(block.id)}
              className="no-print mn-hover-actions inline-flex h-7 w-7 items-center justify-center rounded-full text-ink-muted hover:bg-red-500/12 hover:text-red-600"
            >
              <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>
      </li>
    );
  };

  const unassignedBlocks = blocks.filter(
    (b) => !b.section_id && matchesFilter(b)
  );

  return (
    <section
      aria-label="Structured Meeting Notes"
      className="h-full overflow-y-auto bg-surface-elevated"
    >
      <div className="mx-auto max-w-[740px] space-y-6 px-5 py-6 sm:px-8 sm:py-8">
        {/* Document Header */}
        <header>
          {editingTitle ? (
            <input
              type="text"
              value={titleDraft}
              onChange={(e) => setTitleDraft(e.target.value)}
              onBlur={() => void saveTitle()}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                  void saveTitle();
                }
                if (e.key === "Escape") setEditingTitle(false);
              }}
              autoFocus
              className="mn-field w-full rounded-xl px-3 py-1.5 text-2xl font-semibold tracking-[-0.02em] sm:text-[28px]"
            />
          ) : (
            <h1
              onClick={() => {
                setTitleDraft(title);
                setEditingTitle(true);
              }}
              title="Click to rename meeting"
              className="cursor-text text-2xl font-semibold tracking-[-0.02em] text-ink hover:text-accent sm:text-[28px]"
            >
              {title}
            </h1>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-muted">
            <span className="capitalize font-medium text-ink">{status}</span>
            <span>·</span>
            <span className="tabular-nums">{sections.length} topics</span>
            <span>·</span>
            <span className="tabular-nums">{blocks.length} notes</span>
            {audioAvailable && (
              <>
                <span>·</span>
                <span className="text-emerald-600 dark:text-emerald-400">
                  Audio retained
                </span>
              </>
            )}
          </div>
        </header>

        {/* Editable Executive Summary */}
        <div className="rounded-2xl bg-surface p-4 sm:p-5">
          <div className="flex items-center justify-between gap-2">
            <h2 className="text-[11px] font-semibold uppercase tracking-wider text-ink-muted">
              Summary
            </h2>
            {!editingSummary && executiveSummary && (
              <button
                type="button"
                onClick={() => setEditingSummary(true)}
                className="no-print text-xs font-medium text-ink-muted hover:text-ink"
              >
                Edit
              </button>
            )}
          </div>

          {editingSummary ? (
            <div className="mt-2">
              <RichBlockEditor
                autoFocus
                initialContent={executiveSummary || ""}
                showTypePicker={false}
                showSectionPicker={false}
                placeholder="Summarize outcomes, decisions, and next steps…"
                submitLabel="Save Summary"
                onCancel={() => setEditingSummary(false)}
                onSubmit={({ content }) => saveSummary(content)}
              />
            </div>
          ) : status === "processing" ? (
            <p className="mt-2 animate-pulse text-sm text-accent">
              Consolidating transcript, preserving your locked notes, and writing summary…
            </p>
          ) : executiveSummary ? (
            <p
              onClick={() => setEditingSummary(true)}
              title="Click to edit and format summary"
              className="mt-1.5 cursor-text text-[14.5px] leading-relaxed text-ink"
            >
              <FormattedText text={executiveSummary} />
            </p>
          ) : (
            <p
              onClick={() => setEditingSummary(true)}
              className="mt-1.5 cursor-text text-sm text-ink-muted hover:text-ink"
            >
              Structured notes stream live below. Click <strong>Finish</strong> when done—or click here to draft a summary anytime.
            </p>
          )}
        </div>

        {/* Pinned Moments */}
        {pinnedMoments.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            <span className="mr-1 inline-flex items-center gap-1 text-xs font-medium text-ink-muted">
              <Bookmark className="h-3.5 w-3.5 text-amber-500" aria-hidden="true" />
              Pinned
            </span>
            {pinnedMoments.map((m, idx) => (
              <button
                key={`${m.offset_ms}-${idx}`}
                type="button"
                onClick={() => playAudioAtMs(m.offset_ms)}
                className="rounded-full bg-surface-muted px-2.5 py-1 text-xs font-medium tabular-nums text-ink hover:bg-surface-hover"
              >
                {formatOffsetMs(m.offset_ms)}
                {m.label ? ` · ${m.label}` : ""}
              </button>
            ))}
          </div>
        )}

        {/* Notion-Style Quick Scratchpad Composer */}
        <div className="no-print">
          <RichBlockEditor
            showTypePicker
            showSectionPicker={sections.length > 0}
            clearOnSubmit
            submitLabel="Add"
            placeholder="Jot a formatted note (auto-locked)… Type / for blocks or Cmd+B/I/E"
            onSubmit={async ({
              content,
              blockType,
              sectionId,
              assignee,
              dueDateText,
            }) => {
              const targetSecId =
                sectionId || sections[sections.length - 1]?.id || null;
              const created = await api.createBlock(sessionId, {
                section_id: targetSecId,
                block_type: blockType,
                content,
                assignee: assignee || undefined,
                due_date_text: dueDateText || undefined,
              });
              applyBlockLocal(created);
            }}
          />
        </div>

        {/* Topic Sections */}
        <div className="space-y-7 pt-1">
          {sections.map((sec) => {
            const secBlocks = blocks.filter(
              (b) => b.section_id === sec.id && matchesFilter(b)
            );
            if (activeFilter !== "all" && secBlocks.length === 0) {
              return null;
            }
            return (
              <article
                key={sec.id}
                id={`section-${sec.id}`}
                data-section-id={sec.id}
                className="scroll-mt-4 space-y-2.5"
              >
                <div className="flex items-baseline justify-between gap-2 border-b border-border pb-2">
                  {editingSectionId === sec.id ? (
                    <input
                      type="text"
                      value={sectionDraft}
                      onChange={(e) => setSectionDraft(e.target.value)}
                      onBlur={() => void saveSectionTitle(sec)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                          void saveSectionTitle(sec);
                        }
                        if (e.key === "Escape") setEditingSectionId(null);
                      }}
                      autoFocus
                      className="mn-field flex-1 rounded-lg px-2.5 py-1 text-base font-semibold"
                    />
                  ) : (
                    <h3
                      onClick={() => {
                        setEditingSectionId(sec.id);
                        setSectionDraft(sec.title);
                      }}
                      className="cursor-text text-base font-semibold tracking-[-0.01em] text-ink hover:text-accent"
                    >
                      {sec.title}
                    </h3>
                  )}
                  <span className="tabular-nums text-xs text-ink-muted">
                    {formatOffsetMs(sec.start_ms)}
                  </span>
                </div>

                {secBlocks.length === 0 ? (
                  <p className="px-3 py-1 text-xs text-ink-muted">
                    Listening for notes in this topic…
                  </p>
                ) : (
                  <ul className="space-y-1">{secBlocks.map(renderBlock)}</ul>
                )}

                {addingInSectionId === sec.id ? (
                  <div className="pt-1">
                    <RichBlockEditor
                      autoFocus
                      initialBlockType="bullet"
                      initialSectionId={sec.id}
                      showTypePicker
                      showSectionPicker={false}
                      submitLabel="Add to section"
                      onCancel={() => setAddingInSectionId(null)}
                      onSubmit={async ({
                        content,
                        blockType,
                        assignee,
                        dueDateText,
                      }) => {
                        const created = await api.createBlock(sessionId, {
                          section_id: sec.id,
                          block_type: blockType,
                          content,
                          assignee: assignee || undefined,
                          due_date_text: dueDateText || undefined,
                        });
                        applyBlockLocal(created);
                        setAddingInSectionId(null);
                      }}
                    />
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setAddingInSectionId(sec.id)}
                    className="no-print inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
                  >
                    <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                    <span>Add block</span>
                  </button>
                )}
              </article>
            );
          })}

          {unassignedBlocks.length > 0 && (
            <article className="space-y-2.5">
              <h3 className="border-b border-border pb-2 text-base font-semibold tracking-[-0.01em] text-ink">
                Scratchpad
              </h3>
              <ul className="space-y-1">{unassignedBlocks.map(renderBlock)}</ul>
            </article>
          )}

          {sections.length === 0 && blocks.length === 0 && (
            <div className="py-10 text-center text-sm text-ink-muted">
              Press <strong>Record</strong> or click <strong>Demo</strong> in the bar above to stream live speech and structured notes.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
