import path from "node:path";

import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The UI kit's files import each other as `@/ui/…` and its API types as
// `@/api/generated/initiativeAPI.schemas`, as they do in Initiative. Those
// resolve to the copy in packages/ui; every other `@/` is Hoard's own src.
// tsconfig.json says the same for TypeScript.
const kit = path.resolve(import.meta.dirname, "../../../packages/ui/src");
const src = path.resolve(import.meta.dirname, "src");

// Hoard's image serves its API and these pages from one origin. In dev the
// pages are served here and the API and plug-in routes are proxied to the
// backend, so the pages still reach them on their own origin.
const backend = "http://localhost:8300";

export default defineConfig({
  plugins: [tanstackRouter({ target: "react" }), react(), tailwindcss()],
  resolve: {
    alias: [
      { find: /^@\/ui\//, replacement: `${kit}/ui/` },
      {
        find: /^@\/api\/generated\/initiativeAPI\.schemas$/,
        replacement: `${kit}/api/generated/initiativeAPI.schemas.ts`,
      },
      { find: /^@\//, replacement: `${src}/` },
    ],
  },
  server: {
    // Every interface, so a browser outside a virtual machine reaches it too.
    host: true,
    port: 5300,
    strictPort: true,
    proxy: { "/api": backend, "/v1": backend },
  },
  build: { sourcemap: false },
  test: {
    environment: "jsdom",
    // Testing Library unmounts after each test where `afterEach` is global.
    globals: true,
    css: false,
    restoreMocks: true,
    unstubGlobals: true,
  },
});
