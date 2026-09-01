import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// 产物直接写进 Python 包的 interfaces/web/dist：
// `python -m onto_platform.interfaces.api` 一键启动即可用，运行期零 Node 依赖。
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../src/onto_platform/interfaces/web/dist",
    emptyOutDir: true,
    target: "es2020",
    assetsDir: "assets",
    chunkSizeWarningLimit: 900,
  },
  server: {
    port: 5173,
    proxy: { "/api": "http://127.0.0.1:8100" },   // onto-api 默认端口
  },
});
