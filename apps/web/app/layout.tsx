import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "JobPilot — review queue",
  description: "Review tailored applications before they go out.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="border-b border-rule">
          <div className="mx-auto flex max-w-5xl items-baseline gap-4 px-6 py-4">
            <Link href="/queue" className="text-[15px] font-semibold tracking-tight">
              JobPilot
            </Link>
            <span className="text-[13px] text-muted">
              Phase 0 — review, then apply manually
            </span>
          </div>
        </header>
        <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
