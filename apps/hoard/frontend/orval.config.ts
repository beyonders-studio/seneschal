import { defineConfig } from "orval";

export default defineConfig({
  hoard: {
    input: { target: "./openapi.json" },
    output: {
      target: "./src/api/generated",
      client: "react-query",
      httpClient: "axios",
      mode: "tags-split",
      clean: true,
      override: {
        mutator: { path: "./src/api/mutator.ts", name: "apiMutator" },
        query: { signal: true },
      },
    },
  },
});
