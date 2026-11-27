import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./src/pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/components/**/*.{js,ts,jsx,tsx,mdx}",
    "./src/app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        surface: "var(--surface)",
        "surface-elevated": "var(--surface-elevated)",
        "surface-muted": "var(--surface-muted)",
        "surface-hover": "var(--surface-hover)",
        ink: "var(--ink)",
        "ink-muted": "var(--ink-muted)",
        border: "var(--border)",
        "border-strong": "var(--border-strong)",
        accent: "var(--accent)",
        "accent-soft": "var(--accent-soft)",
      },
    },
  },
  plugins: [],
};

export default config;
