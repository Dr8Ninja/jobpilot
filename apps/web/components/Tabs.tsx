import Link from "next/link";
import type { StatusCount } from "@/lib/api";

/** Nothing is ever deleted — every status stays reachable from here.
 *
 * `Overseas` is not a status: it is the same queue filtered to roles outside
 * India and open remote. It sits alongside the status tabs because that is how
 * the user thinks about it — one more place to look, never a bin. */
const TABS: { key: string; label: string; href: string }[] = [
  { key: "queued", label: "To review", href: "/queue?status=queued" },
  { key: "approved", label: "Approved", href: "/queue?status=approved" },
  { key: "applied", label: "Applied", href: "/queue?status=applied" },
  { key: "not_selected", label: "Shortlist", href: "/queue?status=not_selected" },
  { key: "needs_human", label: "Needs attention", href: "/queue?status=needs_human" },
  { key: "rejected", label: "Rejected", href: "/queue?status=rejected" },
  { key: "", label: "All", href: "/queue?status=all" },
  { key: "overseas", label: "Overseas", href: "/queue?view=overseas" },
];

export function Tabs({
  active,
  counts,
}: {
  active: string;
  counts: StatusCount[];
}) {
  const byStatus = new Map(counts.map((c) => [c.status, c.count]));
  // "overseas" is a pseudo-status the API appends; it must not inflate "All".
  const total = counts
    .filter((c) => c.status !== "overseas")
    .reduce((sum, c) => sum + c.count, 0);

  return (
    <nav className="flex flex-wrap items-center gap-1 border-b border-rule">
      {TABS.map((tab) => {
        const count = tab.key === "" ? total : (byStatus.get(tab.key) ?? 0);
        const selected = active === tab.key;
        return (
          <Link
            key={tab.key || "all"}
            href={tab.href}
            className={`-mb-px border-b-2 px-3 py-2 text-[13px] transition-colors ${
              selected
                ? "border-accent font-medium text-accent"
                : "border-transparent text-muted hover:text-ink"
            }`}
          >
            {tab.label}
            <span className="ml-1.5 tabular-nums opacity-60">{count}</span>
          </Link>
        );
      })}
      <Link
        href="/skills"
        className="-mb-px ml-auto border-b-2 border-transparent px-3 py-2 text-[13px] text-muted transition-colors hover:text-ink"
      >
        Skills to learn →
      </Link>
    </nav>
  );
}
