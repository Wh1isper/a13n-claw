import type { Metadata } from "next";
import { RootProvider } from "fumadocs-ui/provider/next";
import type { ReactNode } from "react";
import "./global.css";

export const metadata: Metadata = {
  metadataBase: new URL("https://a13n-claw.wh1isper.top"),
  title: { default: "a13n Claw", template: "%s · a13n Claw" },
  description:
    "A fresh start for a Harness-based local-first agent runtime. Currently a placeholder package.",
};
export default function Layout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body>
        <RootProvider search={{ options: { type: "static" } }}>
          {children}
        </RootProvider>
      </body>
    </html>
  );
}
