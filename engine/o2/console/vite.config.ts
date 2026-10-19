/// <reference types="vitest/config" />
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

/**
 * The SPA has NO build-time configuration in the co-served case: the built
 * bundle talks to the same origin it was served from, because `axrange serve
 * --spa-dir` serves it beside `/api`. No imprint id, catalog, palette or
 * cluster fact is compiled in.
 *
 * **Two different knobs, deliberately spelled differently** (M1 of the S2
 * review):
 *
 *  - `AXRANGE_DEV_API` (**unprefixed**) is the dev server's PROXY TARGET. It
 *    is read here, at config time, and never reaches the browser -- the app
 *    still calls same-origin `/api/...` and Vite forwards it.
 *  - `VITE_AXRANGE_API` is an ABSOLUTE BASE URL compiled into the bundle
 *    (`src/api/client.ts`). It BYPASSES the proxy and makes the browser talk
 *    cross-origin, which the API does not support -- it installs no CORS
 *    middleware. Use it only against a deployment that fronts the API with
 *    its own CORS/proxy layer.
 *
 * The two were one variable at first, and Vite's `loadEnv` lifts every
 * `VITE_`-prefixed process variable into `import.meta.env` -- so setting the
 * proxy target ALSO compiled it in as the client's base URL, and every dev
 * request went cross-origin past the proxy it was meant to configure.
 */
const DEV_API = process.env.AXRANGE_DEV_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    // Vite 8 defaults to `localhost`, which resolves to `[::1]` on this host
    // -- so `curl http://127.0.0.1:5173` fails while the browser works. Bind
    // the v4 loopback explicitly so both spellings reach the dev server.
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
    // No `globals: true`: every test imports `describe`/`it`/`expect` from
    // `vitest` explicitly, so a test file type-checks under the same strict
    // tsconfig as `src/` with no ambient-global escape hatch.
    globals: false,
  },
});
