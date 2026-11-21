import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@a13n/console": fileURLToPath(
        new URL("./tests/bridge.jsx", import.meta.url),
      ),
    },
  },
  oxc: { jsx: { runtime: "automatic" } },
  test: { environment: "jsdom" },
});
