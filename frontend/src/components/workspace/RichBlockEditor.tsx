"use client";

import { useEffect, useId, useRef, useState } from "react";
import {
  Bold,
  Check,
  Code2,
  Highlighter,
  Italic,
  Link2,
  Strikethrough,
} from "lucide-react";
import { CustomSelect, type CustomSelectOption } from "@/components/ui/CustomSelect";
import { applyInlineFormat, type InlineFormatKind } from "@/lib/inlineFormat";
import { formatOffsetMs } from "@/lib/markdown";
import type { AgendaSection, BlockType } from "@/lib/types";

export const BLOCK_TYPE_OPTIONS: CustomSelectOption[] = [
  {
    value: "scratchpad",
    label: "Note",
    description: "Locked manual note",
    dotColor: "#64748b",
    iconKind: "note",
  },
  {
    value: "bullet",
    label: "Bullet",
    description: "Key point or detail",
    dotColor: "#6b7280",
    iconKind: "bullet",
  },
  {
    value: "decision",
    label: "Decision",
    description: "Agreed outcome",
    dotColor: "#10b981",
    iconKind: "decision",
  },
  {
    value: "action_item",
    label: "Action",
    description: "Task with owner & due",
    dotColor: "#0a66ff",
    iconKind: "action",
  },
  {
    value: "open_question",
    label: "Question",
    description: "Unresolved follow-up",
    dotColor: "#f59e0b",
    iconKind: "question",
  },
];

interface SlashCommandItem {
  id: string;
  label: string;
  hint: string;
  blockType?: BlockType;
  format?: InlineFormatKind;
}

const SLASH_COMMANDS: SlashCommandItem[] = [
  { id: "note", label: "Note", hint: "Manual locked note", blockType: "scratchpad" },
  { id: "bullet", label: "Bullet", hint: "Key discussion bullet", blockType: "bullet" },
  { id: "decision", label: "Decision", hint: "Highlight agreed choice", blockType: "decision" },
  { id: "action", label: "Action item", hint: "Task with @owner & due date", blockType: "action_item" },
  { id: "question", label: "Open question", hint: "Track unresolved question", blockType: "open_question" },
  { id: "bold", label: "Bold", hint: "**bold** text", format: "bold" },
  { id: "highlight", label: "Highlight", hint: "==highlighted== callout", format: "highlight" },
  { id: "code", label: "Inline code", hint: "`code` snippet", format: "code" },
];

interface RichBlockEditorProps {
  initialContent?: string;
  initialBlockType?: BlockType;
  initialSectionId?: string | null;
  initialAssignee?: string | null;
  initialDueDate?: string | null;
  sections?: AgendaSection[];
  showTypePicker?: boolean;
  showSectionPicker?: boolean;
  autoFocus?: boolean;
  placeholder?: string;
  submitLabel?: string;
  clearOnSubmit?: boolean;
  onSubmit: (payload: {
    content: string;
    blockType: BlockType;
    sectionId: string | null;
    assignee: string;
    dueDateText: string;
  }) => Promise<void> | void;
  onCancel?: () => void;
}

export function RichBlockEditor({
  initialContent = "",
  initialBlockType = "scratchpad",
  initialSectionId = "",
  initialAssignee = "",
  initialDueDate = "",
  sections = [],
  showTypePicker = true,
  showSectionPicker = false,
  autoFocus = false,
  placeholder = "Write a note… Type / for blocks, Cmd+B/I/E to format",
  submitLabel = "Save",
  clearOnSubmit = false,
  onSubmit,
  onCancel,
}: RichBlockEditorProps) {
  const editorId = useId();
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const toolbarRef = useRef<HTMLDivElement | null>(null);

  const [content, setContent] = useState(initialContent);
  const [blockType, setBlockType] = useState<BlockType>(initialBlockType);
  const [sectionId, setSectionId] = useState<string>(initialSectionId || "");
  const [assignee, setAssignee] = useState(initialAssignee || "");
  const [dueDateText, setDueDateText] = useState(initialDueDate || "");
  const [saving, setSaving] = useState(false);

  const [slashFilter, setSlashFilter] = useState<string | null>(null);
  const [slashIndex, setSlashIndex] = useState(0);
  const [activeFormats, setActiveFormats] = useState<Record<string, boolean>>({});

  // Sync JS auto-height fallback when field-sizing: content is unsupported
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    if (typeof CSS !== "undefined" && CSS.supports("field-sizing", "content")) {
      return;
    }
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`;
  }, [content]);

  // Roving tabindex fallback for toolbar when native focusgroup is unavailable
  useEffect(() => {
    const bar = toolbarRef.current;
    if (!bar || "focusGroup" in HTMLElement.prototype) return;

    const getItems = () =>
      Array.from(
        bar.querySelectorAll<HTMLElement>("button:not([disabled])")
      );

    const items = getItems();
    items.forEach((b, i) => {
      b.tabIndex = i === 0 ? 0 : -1;
    });

    const onFocusIn = (e: FocusEvent) => {
      const target = e.target as HTMLElement;
      const list = getItems();
      if (list.includes(target)) {
        list.forEach((b) => {
          b.tabIndex = b === target ? 0 : -1;
        });
      }
    };

    const onKeyDown = (e: KeyboardEvent) => {
      const list = getItems();
      const idx = list.indexOf(document.activeElement as HTMLElement);
      if (idx === -1) return;

      const rtl = getComputedStyle(bar).direction === "rtl";
      let nextIdx: number | null = null;
      if (e.key === "ArrowRight") {
        nextIdx = (idx + (rtl ? -1 : 1) + list.length) % list.length;
      } else if (e.key === "ArrowLeft") {
        nextIdx = (idx + (rtl ? 1 : -1) + list.length) % list.length;
      } else if (e.key === "Home") {
        nextIdx = 0;
      } else if (e.key === "End") {
        nextIdx = list.length - 1;
      }
      if (nextIdx !== null) {
        e.preventDefault();
        list[nextIdx]?.focus();
      }
    };

    bar.addEventListener("focusin", onFocusIn);
    bar.addEventListener("keydown", onKeyDown);
    return () => {
      bar.removeEventListener("focusin", onFocusIn);
      bar.removeEventListener("keydown", onKeyDown);
    };
  }, []);

  const refreshActiveFormats = () => {
    const el = textareaRef.current;
    if (!el) return;
    const pos = el.selectionStart ?? 0;
    const around = content.slice(Math.max(0, pos - 40), pos + 40);
    setActiveFormats({
      bold: /\*\*[^*]*$/.test(content.slice(0, pos)) && /^[^*]*\*\*/.test(content.slice(pos)),
      italic: /(^|[^*])\*[^*]*$/.test(content.slice(0, pos)) && /^[^*]*\*([^*]|$)/.test(content.slice(pos)),
      code: /`[^`]*$/.test(content.slice(0, pos)) && /^[^`]*`/.test(content.slice(pos)),
      strike: /~~[^~]*$/.test(content.slice(0, pos)) && /^[^~]*~~/.test(content.slice(pos)),
      highlight: /==[^=]*$/.test(content.slice(0, pos)) && /^[^=]*==/.test(content.slice(pos)) && around.includes("=="),
    });
  };

  const triggerFormat = (kind: InlineFormatKind) => {
    const el = textareaRef.current;
    const start = el ? el.selectionStart : content.length;
    const end = el ? el.selectionEnd : content.length;
    const { nextValue, nextStart, nextEnd } = applyInlineFormat(
      content,
      start,
      end,
      kind
    );
    setContent(nextValue);
    requestAnimationFrame(() => {
      if (!textareaRef.current) return;
      textareaRef.current.focus();
      textareaRef.current.setSelectionRange(nextStart, nextEnd);
      refreshActiveFormats();
    });
  };

  const filteredCommands =
    slashFilter === null
      ? []
      : SLASH_COMMANDS.filter(
          (c) =>
            c.label.toLowerCase().includes(slashFilter) ||
            c.id.includes(slashFilter)
        );

  const runSlashCommand = (cmd: SlashCommandItem) => {
    // Strip the trailing /query token
    const cleaned = content.replace(/(?:^|\s)\/[a-z]*$/i, "").trimEnd();
    setSlashFilter(null);
    if (cmd.blockType) {
      setBlockType(cmd.blockType);
      setContent(cleaned ? `${cleaned} ` : "");
      requestAnimationFrame(() => textareaRef.current?.focus());
    } else if (cmd.format) {
      setContent(cleaned);
      requestAnimationFrame(() => {
        if (!textareaRef.current) return;
        const len = cleaned.length;
        const { nextValue, nextStart, nextEnd } = applyInlineFormat(
          cleaned,
          len,
          len,
          cmd.format!
        );
        setContent(nextValue);
        requestAnimationFrame(() => {
          textareaRef.current?.focus();
          textareaRef.current?.setSelectionRange(nextStart, nextEnd);
        });
      });
    }
  };

  const handleSubmit = async () => {
    const trimmed = content.trim();
    if (!trimmed || saving) return;
    setSaving(true);
    try {
      await onSubmit({
        content: trimmed,
        blockType,
        sectionId: sectionId || null,
        assignee: assignee.trim(),
        dueDateText: dueDateText.trim(),
      });
      if (clearOnSubmit) {
        setContent("");
        setAssignee("");
        setDueDateText("");
        setSlashFilter(null);
      }
    } finally {
      setSaving(false);
    }
  };

  const sectionOptions: CustomSelectOption[] = [
    {
      value: "",
      label: "Latest section",
      description: "Attach to active topic",
    },
    ...sections.map((s) => ({
      value: s.id,
      label: s.title,
      meta: formatOffsetMs(s.start_ms),
    })),
  ];

  return (
    <div className="relative rounded-2xl border border-border-strong bg-surface-elevated p-2.5 shadow-xs transition-shadow focus-within:border-accent focus-within:shadow-[0_0_0_3px_var(--accent-soft)]">
      {/* Top Formatting & Block Metadata Toolbar */}
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border pb-2">
        <div
          ref={toolbarRef}
          role="toolbar"
          {...({ focusgroup: "toolbar wrap" } as Record<string, string>)}
          aria-label="Text formatting"
          aria-controls={editorId}
          className="mn-toolbar"
        >
          <div role="group" aria-label="Inline text styles">
            <button
              type="button"
              aria-pressed={Boolean(activeFormats.bold)}
              aria-label="Bold"
              title="Bold (Cmd/Ctrl+B)"
              onClick={() => triggerFormat("bold")}
            >
              <Bold className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-pressed={Boolean(activeFormats.italic)}
              aria-label="Italic"
              title="Italic (Cmd/Ctrl+I)"
              onClick={() => triggerFormat("italic")}
            >
              <Italic className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-pressed={Boolean(activeFormats.strike)}
              aria-label="Strikethrough"
              title="Strikethrough"
              onClick={() => triggerFormat("strike")}
            >
              <Strikethrough className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-pressed={Boolean(activeFormats.code)}
              aria-label="Inline code"
              title="Inline code (Cmd/Ctrl+E)"
              onClick={() => triggerFormat("code")}
            >
              <Code2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
            <button
              type="button"
              aria-pressed={Boolean(activeFormats.highlight)}
              aria-label="Highlight"
              title="Highlight (Cmd/Ctrl+Shift+H)"
              onClick={() => triggerFormat("highlight")}
            >
              <Highlighter className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>

          <span role="separator" aria-orientation="vertical" />

          <div role="group" aria-label="Insert elements">
            <button
              type="button"
              aria-label="Insert link"
              title="Insert link"
              onClick={() => triggerFormat("link")}
            >
              <Link2 className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </div>
        </div>

        {(showTypePicker || (showSectionPicker && sections.length > 0)) && (
          <div className="flex flex-wrap items-center gap-1.5">
            {showTypePicker && (
              <CustomSelect
                ariaLabel="Note block type"
                value={blockType}
                onChange={(v) => setBlockType(v as BlockType)}
                options={BLOCK_TYPE_OPTIONS}
              />
            )}
            {showSectionPicker && sections.length > 0 && (
              <CustomSelect
                ariaLabel="Target agenda section"
                value={sectionId}
                onChange={setSectionId}
                options={sectionOptions}
              />
            )}
          </div>
        )}
      </div>

      {/* Auto-Growing Notion Textarea */}
      <div className="relative pt-2">
        <label htmlFor={editorId} className="sr-only">
          Note content
        </label>
        <textarea
          ref={textareaRef}
          id={editorId}
          value={content}
          autoFocus={autoFocus}
          placeholder={placeholder}
          onSelect={refreshActiveFormats}
          onClick={refreshActiveFormats}
          onChange={(e) => {
            const val = e.target.value;
            setContent(val);
            const slashMatch = val.match(/(?:^|\s)\/([a-z]*)$/i);
            if (slashMatch) {
              setSlashFilter(slashMatch[1].toLowerCase());
              setSlashIndex(0);
            } else {
              setSlashFilter(null);
            }
          }}
          onKeyDown={(e) => {
            if (slashFilter !== null && filteredCommands.length > 0) {
              if (e.key === "ArrowDown") {
                e.preventDefault();
                setSlashIndex((i) => (i + 1) % filteredCommands.length);
                return;
              }
              if (e.key === "ArrowUp") {
                e.preventDefault();
                setSlashIndex(
                  (i) => (i - 1 + filteredCommands.length) % filteredCommands.length
                );
                return;
              }
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                runSlashCommand(filteredCommands[slashIndex]);
                return;
              }
              if (e.key === "Escape") {
                e.preventDefault();
                setSlashFilter(null);
                return;
              }
            }

            if (e.metaKey || e.ctrlKey) {
              const key = e.key.toLowerCase();
              if (key === "b") {
                e.preventDefault();
                triggerFormat("bold");
                return;
              }
              if (key === "i") {
                e.preventDefault();
                triggerFormat("italic");
                return;
              }
              if (key === "e") {
                e.preventDefault();
                triggerFormat("code");
                return;
              }
              if (e.shiftKey && key === "h") {
                e.preventDefault();
                triggerFormat("highlight");
                return;
              }
            }

            if (
              e.key === "Enter" &&
              !e.shiftKey &&
              !e.nativeEvent.isComposing
            ) {
              e.preventDefault();
              void handleSubmit();
              return;
            }
            if (e.key === "Escape" && onCancel) {
              e.preventDefault();
              onCancel();
            }
          }}
          className="mn-textarea bg-transparent px-1.5 text-[14.5px] text-ink placeholder:text-ink-muted focus:outline-none"
        />

        {/* Notion Slash-Command Menu */}
        {slashFilter !== null && filteredCommands.length > 0 && (
          <div
            role="listbox"
            aria-label="Slash commands"
            className="absolute left-1 top-full z-20 mt-1 w-64 rounded-2xl border border-border-strong bg-surface-elevated p-1.5 shadow-xl"
          >
            <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-ink-muted">
              Turn into / Insert
            </div>
            {filteredCommands.map((cmd, idx) => (
              <button
                key={cmd.id}
                type="button"
                role="option"
                aria-selected={idx === slashIndex}
                onMouseDown={(e) => {
                  e.preventDefault();
                  runSlashCommand(cmd);
                }}
                className={`flex w-full items-center justify-between rounded-xl px-2.5 py-1.5 text-left text-xs ${
                  idx === slashIndex
                    ? "bg-accent-soft text-accent font-medium"
                    : "text-ink hover:bg-surface-muted"
                }`}
              >
                <span>{cmd.label}</span>
                <span className="text-[11px] text-ink-muted">{cmd.hint}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {/* Action Item Metadata + Submit Controls */}
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-2">
        <div className="flex flex-wrap items-center gap-2">
          {blockType === "action_item" ? (
            <>
              <input
                type="text"
                aria-label="Assignee"
                value={assignee}
                onChange={(e) => setAssignee(e.target.value)}
                placeholder="@assignee"
                className="mn-field h-7 w-28 rounded-lg px-2 text-xs"
              />
              <input
                type="text"
                aria-label="Due date"
                value={dueDateText}
                onChange={(e) => setDueDateText(e.target.value)}
                placeholder="Due (e.g. Fri)"
                className="mn-field h-7 w-28 rounded-lg px-2 text-xs"
              />
            </>
          ) : (
            <span className="px-1 text-[11px] text-ink-muted">
              Enter to save · Shift+Enter newline · / commands
            </span>
          )}
        </div>

        <div className="flex items-center gap-1.5">
          {onCancel && (
            <button
              type="button"
              onClick={onCancel}
              className="min-h-[30px] rounded-full px-3 text-xs font-medium text-ink-muted hover:bg-surface-muted hover:text-ink"
            >
              Cancel
            </button>
          )}
          <button
            type="button"
            disabled={saving || !content.trim()}
            onClick={() => void handleSubmit()}
            className="inline-flex min-h-[30px] items-center gap-1 rounded-full bg-accent px-3.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-40"
          >
            <Check className="h-3.5 w-3.5" aria-hidden="true" />
            <span>{saving ? "Saving…" : submitLabel}</span>
          </button>
        </div>
      </div>
    </div>
  );
}
