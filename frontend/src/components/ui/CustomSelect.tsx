"use client";

import { useLayoutEffect, useRef } from "react";

export type SelectIconKind =
  | "note"
  | "bullet"
  | "decision"
  | "action"
  | "question";

export interface CustomSelectOption {
  value: string;
  label: string;
  description?: string;
  dotColor?: string;
  iconKind?: SelectIconKind;
  meta?: string;
}

interface CustomSelectProps {
  id?: string;
  name?: string;
  ariaLabel: string;
  value: string;
  onChange: (value: string) => void;
  options: CustomSelectOption[];
  className?: string;
}

const SVG_NS = "http://www.w3.org/2000/svg";

function buildIconSvg(kind: SelectIconKind, color?: string): SVGSVGElement {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("viewBox", "0 0 20 20");
  svg.setAttribute("width", "14");
  svg.setAttribute("height", "14");
  svg.setAttribute("fill", "none");
  svg.setAttribute("stroke", color || "currentColor");
  svg.setAttribute("stroke-width", "1.75");
  svg.setAttribute("stroke-linecap", "round");
  svg.setAttribute("stroke-linejoin", "round");
  svg.setAttribute("aria-hidden", "true");

  const addPath = (d: string) => {
    const p = document.createElementNS(SVG_NS, "path");
    p.setAttribute("d", d);
    svg.appendChild(p);
  };

  switch (kind) {
    case "decision":
      addPath("M10 17.5a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15Z");
      addPath("m7 10.2 2 2 4-4");
      break;
    case "action":
      addPath("M4.5 3.5h11a1 1 0 0 1 1 1v11a1 1 0 0 1-1 1h-11a1 1 0 0 1-1-1v-11a1 1 0 0 1 1-1Z");
      addPath("m7 10.2 2 2 4-4");
      break;
    case "question":
      addPath("M10 17.5a7.5 7.5 0 1 0 0-15 7.5 7.5 0 0 0 0 15Z");
      addPath("M7.9 7.8a2.2 2.2 0 0 1 4.2.7c0 1.4-2.1 2-2.1 3");
      addPath("M10 14.2h.01");
      break;
    case "bullet":
      addPath("M7.5 5.5h9");
      addPath("M7.5 10h9");
      addPath("M7.5 14.5h9");
      addPath("M3.8 5.5h.01");
      addPath("M3.8 10h.01");
      addPath("M3.8 14.5h.01");
      break;
    case "note":
    default:
      addPath("M5.5 3.5h6.5L15.5 7v9.5a1 1 0 0 1-1 1h-9a1 1 0 0 1-1-1v-12a1 1 0 0 1 1-1Z");
      addPath("M12 3.5V7h3.5");
      addPath("M7.5 10.5h5");
      addPath("M7.5 13.5h3.5");
      break;
  }
  return svg;
}

export function CustomSelect({
  id,
  name,
  ariaLabel,
  value,
  onChange,
  options,
  className = "",
}: CustomSelectProps) {
  const selectRef = useRef<HTMLSelectElement | null>(null);

  useLayoutEffect(() => {
    const selectEl = selectRef.current;
    if (!selectEl) return;

    const supportsBaseSelect =
      typeof CSS !== "undefined" && CSS.supports("appearance", "base-select");

    const frag = document.createDocumentFragment();

    if (supportsBaseSelect) {
      const btn = document.createElement("button");
      btn.type = "button";
      const selectedContent = document.createElement("selectedcontent");
      btn.appendChild(selectedContent);
      frag.appendChild(btn);
    }

    for (const opt of options) {
      const optEl = document.createElement("option");
      optEl.value = opt.value;

      if (!supportsBaseSelect) {
        optEl.textContent = opt.meta ? `${opt.label} (${opt.meta})` : opt.label;
        frag.appendChild(optEl);
        continue;
      }

      if (opt.iconKind) {
        const iconWrap = document.createElement("span");
        iconWrap.className = "inline-flex shrink-0 items-center";
        iconWrap.setAttribute("aria-hidden", "true");
        iconWrap.appendChild(buildIconSvg(opt.iconKind, opt.dotColor));
        optEl.appendChild(iconWrap);
      } else if (opt.dotColor) {
        const dot = document.createElement("span");
        dot.className = "h-2 w-2 shrink-0 rounded-full";
        dot.style.backgroundColor = opt.dotColor;
        dot.setAttribute("aria-hidden", "true");
        optEl.appendChild(dot);
      }

      const textCol = document.createElement("span");
      textCol.className = "flex min-w-0 flex-1 flex-col text-left";

      const titleSpan = document.createElement("span");
      titleSpan.className = "mn-opt-title truncate";
      titleSpan.textContent = opt.label;
      if (opt.description || opt.meta) {
        const srSep = document.createElement("span");
        srSep.className = "sr-only";
        srSep.textContent = ". ";
        titleSpan.appendChild(srSep);
      }
      textCol.appendChild(titleSpan);

      if (opt.description) {
        const descSpan = document.createElement("span");
        descSpan.className = "mn-opt-desc text-[11px] font-normal text-ink-muted";
        descSpan.textContent = opt.description;
        textCol.appendChild(descSpan);
      }

      optEl.appendChild(textCol);

      if (opt.meta) {
        const metaSpan = document.createElement("span");
        metaSpan.className =
          "mn-opt-meta tabular-nums shrink-0 text-[11px] text-ink-muted";
        metaSpan.textContent = opt.meta;
        optEl.appendChild(metaSpan);
      }

      frag.appendChild(optEl);
    }

    selectEl.replaceChildren(frag);
    selectEl.value = value;
  }, [options, value]);

  return (
    <select
      ref={selectRef}
      id={id}
      name={name}
      aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.value)}
      className={`mn-select ${className}`}
    />
  );
}
