import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

// On Windows Vite/esbuild can fail to remove its temp directory when %TEMP%
// lives on another volume or is protected. Keep build temp inside the project.
const tmp = resolve(".vite-tmp");
mkdirSync(tmp, { recursive: true });
process.env.TMP = tmp;
process.env.TEMP = tmp;
process.env.TMPDIR = tmp;

const { build } = await import("vite");
await build({ mode: "production" });
