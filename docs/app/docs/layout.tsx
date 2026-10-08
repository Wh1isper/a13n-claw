import { DocsLayout } from "fumadocs-ui/layouts/docs";
import type { ReactNode } from "react";
import { source } from "@/lib/source";
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <DocsLayout
      tree={source.pageTree}
      nav={{ title: "a13n Claw", url: "/" }}
      githubUrl="https://github.com/Wh1isper/a13n-claw"
    >
      {children}
    </DocsLayout>
  );
}
