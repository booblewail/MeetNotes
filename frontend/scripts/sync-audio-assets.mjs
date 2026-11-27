import fs from "node:fs";
import path from "node:path";

const root = process.cwd();
const outDir = path.join(root, "public", "audio");
fs.mkdirSync(outDir, { recursive: true });

const copyTargets = [
  {
    srcDir: path.join(root, "node_modules", "@ricky0123", "vad-web", "dist"),
    patterns: [/\.onnx$/, /\.worklet\.bundle\.min\.js$/],
  },
  {
    srcDir: path.join(root, "node_modules", "onnxruntime-web", "dist"),
    patterns: [/^ort-wasm.*\.(wasm|mjs|js)$/],
  },
];

let copied = 0;
for (const { srcDir, patterns } of copyTargets) {
  if (!fs.existsSync(srcDir)) continue;
  for (const entry of fs.readdirSync(srcDir)) {
    if (patterns.some((re) => re.test(entry))) {
      fs.copyFileSync(path.join(srcDir, entry), path.join(outDir, entry));
      copied += 1;
    }
  }
}

// Ensure canonical silero_vad.onnx filename exists if a versioned/legacy filename was shipped
const canonicalOnnx = path.join(outDir, "silero_vad.onnx");
if (!fs.existsSync(canonicalOnnx)) {
  const anyOnnx = fs
    .readdirSync(outDir)
    .find((f) => f.includes("silero_vad") && f.endsWith(".onnx"));
  if (anyOnnx) {
    fs.copyFileSync(path.join(outDir, anyOnnx), canonicalOnnx);
  }
}

console.log(`[sync-audio-assets] synced ${copied} VAD/ORT runtime asset(s) into public/audio/`);
