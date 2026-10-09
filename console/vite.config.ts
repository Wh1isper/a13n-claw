import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8080" } },
  build: {
    outDir: fileURLToPath(
      new URL("../a13n_claw/static/console", import.meta.url),
    ),
    emptyOutDir: true,
  },
});
