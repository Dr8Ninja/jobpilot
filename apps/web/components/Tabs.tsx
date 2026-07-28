import Link from "next/link";
import type { StatusCount } from "@/lib/api";

/** Nothing is ever deleted — every status stays reachable from here. */
const TABS: { key: string; label: string }[] = [
  { key: "queued", label: "To review" },
  { key: "approved", label: "Approved" },
  { key: "applied", label: "Applied" },
  { key: "not_selected", label: "Shortlist" },
  { key: "needs_human", label: "Needs attention" },
  { key: "rejected", label: "Rejected" },
  { key: "", label: "All" },
];

export function Tabs({
  active,
  counts,
}: {
  active: string;
  counts: StatusCount[];
}) {
  const byStatus = new Map(counts.map((c) => [c.status, c.count]));
  const total = counts.reduce((sum, c) => sum + c.count, 0);

  return (
    <nav className="flex flex-wrap gap-1 border-b border-rule">
      {TABS.map((tab) => {
        const count = tab.key === "" ? total : (byStatus.get(tab.key) ?? 0);
        const selected = active === tab.key;
        return (
          <Link
            key={tab.key || "all"}
            href={tab.key ? `/queue?status=${tab.key}` : "/queue"}
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
    </nav>
  );
}
