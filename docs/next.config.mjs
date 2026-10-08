import { fileURLToPath } from "node:url";
import { createMDX } from "fumadocs-mdx/next";
const root = fileURLToPath(new URL(".", import.meta.url));
export default createMDX()({
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  agentRules: false,
  outputFileTracingRoot: root,
  turbopack: { root },
});
