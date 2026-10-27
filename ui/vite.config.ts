/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * No build-time configuration (ui/Spec.md "Config"): the bundle calls the
 * same origin it was served from, because `axrange serve --spa-dir ui/dist`
 * co-serves it beside `/api`. Under `npm run dev` the proxy below forwards
 * `/api` to a running `axrange serve`; `AXRANGE_DEV_API` (unprefixed, read
 * here at config time, never shipped to the browser) moves that target.
 */
const DEV_API = process.env.AXRANGE_DEV_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": { target: DEV_API, changeOrigin: false },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
  test: {
    environment: "jsdom",
    include: ["test/**/*.test.ts", "test/**/*.test.tsx"],
    setupFiles: ["test/setup.ts"],
    globals: false,
  },
});
