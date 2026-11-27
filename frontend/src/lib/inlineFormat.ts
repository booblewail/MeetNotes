export type InlineFormatKind =
  | "bold"
  | "italic"
  | "code"
  | "strike"
  | "highlight"
  | "link";

export type InlineToken =
  | { type: "text"; text: string }
  | { type: "bold"; text: string }
  | { type: "italic"; text: string }
  | { type: "code"; text: string }
  | { type: "strike"; text: string }
  | { type: "highlight"; text: string }
  | { type: "link"; label: string; href: string };

const TOKEN_REGEX =
  /(\*\*[^*\n]+?\*\*|~~[^~\n]+?~~|==[^=\n]+?==|`[^`\n]+?`|\*[^*\n]+?\*|\[[^\]\n]+\]\(https?:\/\/[^\s)]+\))/g;

export function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function parseInlineTokens(input: string): InlineToken[] {
  if (!input) return [];
  const tokens: InlineToken[] = [];
  let cursor = 0;

  for (const match of input.matchAll(TOKEN_REGEX)) {
    const idx = match.index ?? 0;
    if (idx > cursor) {
      tokens.push({ type: "text", text: input.slice(cursor, idx) });
    }

    const raw = match[0];
    if (raw.startsWith("**") && raw.endsWith("**")) {
      tokens.push({ type: "bold", text: raw.slice(2, -2) });
    } else if (raw.startsWith("~~") && raw.endsWith("~~")) {
      tokens.push({ type: "strike", text: raw.slice(2, -2) });
    } else if (raw.startsWith("==") && raw.endsWith("==")) {
      tokens.push({ type: "highlight", text: raw.slice(2, -2) });
    } else if (raw.startsWith("`") && raw.endsWith("`")) {
      tokens.push({ type: "code", text: raw.slice(1, -1) });
    } else if (raw.startsWith("*") && raw.endsWith("*")) {
      tokens.push({ type: "italic", text: raw.slice(1, -1) });
    } else if (raw.startsWith("[")) {
      const sep = raw.indexOf("](");
      const label = raw.slice(1, sep);
      const href = raw.slice(sep + 2, -1);
      if (isSafeHttpUrl(href)) {
        tokens.push({ type: "link", label, href });
      } else {
        tokens.push({ type: "text", text: raw });
      }
    } else {
      tokens.push({ type: "text", text: raw });
    }

    cursor = idx + raw.length;
  }

  if (cursor < input.length) {
    tokens.push({ type: "text", text: input.slice(cursor) });
  }
  return tokens;
}

const MARKERS: Record<
  Exclude<InlineFormatKind, "link">,
  { prefix: string; suffix: string; placeholder: string }
> = {
  bold: { prefix: "**", suffix: "**", placeholder: "bold text" },
  italic: { prefix: "*", suffix: "*", placeholder: "italic text" },
  code: { prefix: "`", suffix: "`", placeholder: "code" },
  strike: { prefix: "~~", suffix: "~~", placeholder: "strikethrough" },
  highlight: { prefix: "==", suffix: "==", placeholder: "highlighted" },
};

export function applyInlineFormat(
  value: string,
  start: number,
  end: number,
  kind: InlineFormatKind,
  linkUrl = "https://example.com"
): { nextValue: string; nextStart: number; nextEnd: number } {
  const safeStart = Math.max(0, Math.min(start, value.length));
  const safeEnd = Math.max(safeStart, Math.min(end, value.length));
  const selected = value.slice(safeStart, safeEnd);

  if (kind === "link") {
    const label = selected || "link text";
    const replacement = `[${label}](${linkUrl})`;
    const nextValue =
      value.slice(0, safeStart) + replacement + value.slice(safeEnd);
    const urlStart = safeStart + label.length + 3;
    return {
      nextValue,
      nextStart: urlStart,
      nextEnd: urlStart + linkUrl.length,
    };
  }

  const { prefix, suffix, placeholder } = MARKERS[kind];
  const pLen = prefix.length;
  const sLen = suffix.length;

  // Toggle off if selection itself is wrapped
  if (
    selected.length >= pLen + sLen &&
    selected.startsWith(prefix) &&
    selected.endsWith(suffix)
  ) {
    const inner = selected.slice(pLen, selected.length - sLen);
    return {
      nextValue: value.slice(0, safeStart) + inner + value.slice(safeEnd),
      nextStart: safeStart,
      nextEnd: safeStart + inner.length,
    };
  }

  // Toggle off if immediately surrounded by markers
  if (
    safeStart >= pLen &&
    value.slice(safeStart - pLen, safeStart) === prefix &&
    value.slice(safeEnd, safeEnd + sLen) === suffix
  ) {
    const nextValue =
      value.slice(0, safeStart - pLen) +
      selected +
      value.slice(safeEnd + sLen);
    return {
      nextValue,
      nextStart: safeStart - pLen,
      nextEnd: safeEnd - pLen,
    };
  }

  const inner = selected || placeholder;
  const wrapped = `${prefix}${inner}${suffix}`;
  return {
    nextValue: value.slice(0, safeStart) + wrapped + value.slice(safeEnd),
    nextStart: safeStart + pLen,
    nextEnd: safeStart + pLen + inner.length,
  };
}
