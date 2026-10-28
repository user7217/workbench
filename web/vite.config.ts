/// <reference types="vitest/config" />
import path from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/**
 * The look (src/ui by default) is swappable: WORKBENCH_UI=<folder relative to web/> picks another
 * folder whose index.tsx default-exports the view, e.g. `WORKBENCH_UI=examples/minimal-look npm run build`.
 * The core is always src/core.
 */
const uiDir = path.resolve(__dirname, process.env.WORKBENCH_UI || "src/ui");

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@workbench-ui": uiDir,
      "@workbench-core": path.resolve(__dirname, "src/core"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.WORKBENCH_API ?? "http://127.0.0.1:8420",
        changeOrigin: true,
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/__tests__/**/*.test.ts"],
  },
});
