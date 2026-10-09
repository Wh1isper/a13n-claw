import { fileURLToPath, URL } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: fileURLToPath(
      new URL("../a13n_claw/static/console", import.meta.url),
    ),
    emptyOutDir: true,
  },
});
