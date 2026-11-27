"use client";

import { useEffect, useRef, useState } from "react";
import { Check, Copy, Download, Printer, X } from "lucide-react";
import { api } from "@/lib/api";
import { serializeSessionMarkdown } from "@/lib/markdown";
import type { ExportFormat } from "@/lib/types";
import { useSessionStore } from "@/store/sessionStore";

interface ExportModalProps {
  sessionId: string;
  open: boolean;
  onClose: () => void;
}

export function ExportModal({ sessionId, open, onClose }: ExportModalProps) {
  const dialogRef = useRef<HTMLDialogElement | null>(null);
  const title = useSessionStore((s) => s.title);
  const status = useSessionStore((s) => s.status);
  const durationMs = useSessionStore((s) => s.durationMs);
  const createdAt = useSessionStore((s) => s.createdAt);
  const executiveSummary = useSessionStore((s) => s.executiveSummary);
  const pinnedMoments = useSessionStore((s) => s.pinnedMoments);
  const sections = useSessionStore((s) => s.sections);
  const blocks = useSessionStore((s) => s.blocks);

  const [activeFormat, setActiveFormat] = useState<ExportFormat>("markdown");
  const [content, setContent] = useState("");
  const [filename, setFilename] = useState("meeting-notes.md");
  const [loading, setLoading] = useState(false);
  const [copied, setCopied] = useState(false);

  const loadFormat = async (fmt: ExportFormat) => {
    setActiveFormat(fmt);
    if (fmt === "markdown") {
      const md = serializeSessionMarkdown({
        title,
        status,
        durationMs,
        createdAt,
        executiveSummary,
        pinnedMoments,
        sections,
        blocks,
      });
      setContent(md);
      setFilename(
        `${title.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "meeting"}.md`
      );
      return;
    }

    setLoading(true);
    try {
      const res = await api.exportSession(sessionId, fmt);
      setContent(res.content);
      setFilename(res.filename);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (open) {
      if (!dialog.open) dialog.showModal();
      void loadFormat("markdown");
    } else if (dialog.open) {
      dialog.close();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const dialog = dialogRef.current;
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

  const handleCopy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 1800);
  };

  const handleDownload = () => {
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
  };

  return (
    <dialog
      ref={dialogRef}
      {...({ closedby: "any" } as Record<string, string>)}
      aria-labelledby="export-dialog-title"
      onClose={() => {
        if (dialogRef.current) {
          dialogRef.current.returnValue = "";
        }
        onClose();
      }}
      className="no-print w-full max-w-2xl rounded-3xl border border-border bg-surface-elevated p-6 text-ink shadow-2xl backdrop:bg-black/50 backdrop:backdrop-blur-xs"
    >
      <div className="flex items-center justify-between gap-4">
        <h1
          id="export-dialog-title"
          className="text-lg font-semibold tracking-[-0.01em]"
        >
          Export Notes
        </h1>
        <button
          type="button"
          aria-label="Close export modal"
          onClick={() => dialogRef.current?.close("close")}
          className="inline-flex h-8 w-8 items-center justify-center rounded-full text-ink-muted hover:bg-surface-muted hover:text-ink"
        >
          <X className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>

      {/* Segmented Format Control */}
      <div className="mt-4 inline-flex rounded-full bg-surface-muted p-1">
        {(
          [
            { id: "markdown", label: "Markdown" },
            { id: "slack_recap", label: "Slack Update" },
            { id: "followup_email", label: "Follow-up Email" },
          ] as const
        ).map((tab) => (
          <button
            key={tab.id}
            type="button"
            onClick={() => void loadFormat(tab.id)}
            className={`min-h-[34px] rounded-full px-3.5 text-xs font-medium ${
              activeFormat === tab.id
                ? "bg-surface-elevated text-ink shadow-xs"
                : "text-ink-muted hover:text-ink"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="mt-4">
        <label htmlFor="export-preview" className="sr-only">
          Export Preview
        </label>
        <textarea
          id="export-preview"
          readOnly
          value={loading ? "Generating recap…" : content}
          rows={12}
          className="w-full rounded-2xl border border-border bg-surface p-4 font-mono text-xs leading-relaxed text-ink focus:outline-none"
        />
      </div>

      <div className="mt-5 flex flex-wrap items-center justify-between gap-3">
        <button
          type="button"
          onClick={() => {
            dialogRef.current?.close();
            setTimeout(() => window.print(), 80);
          }}
          className="inline-flex min-h-[40px] items-center gap-2 rounded-full border border-border-strong px-4 text-xs font-medium text-ink hover:bg-surface-muted"
        >
          <Printer className="h-3.5 w-3.5" aria-hidden="true" />
          <span>Print / PDF</span>
        </button>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleDownload}
            className="inline-flex min-h-[40px] items-center gap-2 rounded-full border border-border-strong px-4 text-xs font-medium text-ink hover:bg-surface-muted"
          >
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            <span>Download</span>
          </button>
          <button
            type="button"
            autoFocus
            onClick={() => void handleCopy()}
            className="inline-flex min-h-[40px] items-center gap-2 rounded-full bg-accent px-5 text-xs font-medium text-white hover:opacity-90"
          >
            {copied ? (
              <>
                <Check className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Copied</span>
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                <span>Copy</span>
              </>
            )}
          </button>
        </div>
      </div>
    </dialog>
  );
}
