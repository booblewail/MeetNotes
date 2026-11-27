import type {
  AgendaSection,
  NoteBlock,
  PinnedMoment,
  SessionStatus,
} from "./types.ts";

export function formatOffsetMs(ms: number): string {
  const totalSec = Math.max(0, Math.floor(ms / 1000));
  const mins = Math.floor(totalSec / 60);
  const secs = totalSec % 60;
  return `${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

export interface MarkdownDocumentInput {
  title: string;
  status: SessionStatus;
  durationMs: number;
  createdAt?: string;
  executiveSummary: string | null;
  pinnedMoments: PinnedMoment[];
  sections: AgendaSection[];
  blocks: NoteBlock[];
}

export function serializeSessionMarkdown(doc: MarkdownDocumentInput): string {
  const lines: string[] = [
    `# ${doc.title}`,
    "",
    `- **Duration:** ${formatOffsetMs(doc.durationMs)}`,
    `- **Status:** ${doc.status}`,
  ];
  if (doc.createdAt) {
    lines.push(`- **Date:** ${doc.createdAt}`);
  }
  lines.push("");

  if (doc.executiveSummary && doc.executiveSummary.trim()) {
    lines.push("## Executive Summary", "", doc.executiveSummary.trim(), "");
  }

  if (doc.pinnedMoments.length > 0) {
    lines.push("## Pinned Moments", "");
    for (const m of doc.pinnedMoments) {
      lines.push(
        `- **[${formatOffsetMs(m.offset_ms)}]** ${m.label || "Key moment"}`
      );
    }
    lines.push("");
  }

  const orderedSections = [...doc.sections].sort(
    (a, b) => a.order_index - b.order_index || a.start_ms - b.start_ms
  );
  const orderedBlocks = [...doc.blocks].sort(
    (a, b) => a.order_index - b.order_index
  );

  for (const sec of orderedSections) {
    lines.push(`## ${sec.title} (${formatOffsetMs(sec.start_ms)})`, "");
    const secBlocks = orderedBlocks.filter((b) => b.section_id === sec.id);
    for (const b of secBlocks) {
      lines.push(formatBlockLine(b));
    }
    lines.push("");
  }

  const unassigned = orderedBlocks.filter((b) => !b.section_id);
  if (unassigned.length > 0) {
    lines.push("## Scratchpad", "");
    for (const b of unassigned) {
      lines.push(formatBlockLine(b));
    }
    lines.push("");
  }

  return `${lines.join("\n").trim()}\n`;
}

function formatBlockLine(b: NoteBlock): string {
  const lock = b.is_user_edited ? " [Locked]" : "";
  if (b.block_type === "decision") {
    return `- **Decision:** ${b.content}${lock}`;
  }
  if (b.block_type === "action_item") {
    const check = b.completed ? "x" : " ";
    const meta: string[] = [];
    if (b.assignee) meta.push(`@${b.assignee}`);
    if (b.due_date_text) meta.push(`due ${b.due_date_text}`);
    const suffix = meta.length > 0 ? ` (${meta.join(", ")})` : "";
    return `- [${check}] ${b.content}${suffix}${lock}`;
  }
  if (b.block_type === "open_question") {
    return `- **Open Question:** ${b.content}${lock}`;
  }
  return `- ${b.content}${lock}`;
}
