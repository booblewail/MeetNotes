"use client";

import { Fragment } from "react";
import { parseInlineTokens } from "@/lib/inlineFormat";

export function FormattedText({ text }: { text: string }) {
  const tokens = parseInlineTokens(text);
  if (tokens.length === 0) return null;

  return (
    <>
      {tokens.map((tok, idx) => {
        switch (tok.type) {
          case "bold":
            return (
              <strong key={idx} className="font-semibold text-ink">
                {tok.text}
              </strong>
            );
          case "italic":
            return (
              <em key={idx} className="italic">
                {tok.text}
              </em>
            );
          case "code":
            return (
              <code
                key={idx}
                className="rounded-md bg-surface-muted px-1.5 py-0.5 font-mono text-[0.86em] text-ink"
              >
                {tok.text}
              </code>
            );
          case "strike":
            return (
              <del key={idx} className="text-ink-muted line-through">
                {tok.text}
              </del>
            );
          case "highlight":
            return (
              <mark
                key={idx}
                className="rounded-xs bg-amber-300/45 px-1 text-ink dark:bg-amber-400/25"
              >
                {tok.text}
              </mark>
            );
          case "link":
            return (
              <a
                key={idx}
                href={tok.href}
                target="_blank"
                rel="noopener noreferrer"
                onClick={(e) => e.stopPropagation()}
                className="underline decoration-accent/60 underline-offset-2 text-accent hover:decoration-accent"
              >
                {tok.label}
              </a>
            );
          default:
            return <Fragment key={idx}>{tok.text}</Fragment>;
        }
      })}
    </>
  );
}
