import Link from "next/link";
import { Badge, ScorePill, StatusBadge } from "@/components/Badges";
import { Tabs } from "@/components/Tabs";
import {
  getCounts,
  getQueue,
  postedAge,
  PREFERRED_LOCATIONS,
  type QueueCard,
  type StatusCount,
} from "@/lib/api";

export const dynamic = "force-dynamic";

function Row({ card }: { card: QueueCard }) {
  return (
    <Link
      href={`/queue/${card.application_id}`}
      className="grid grid-cols-[1fr_auto] items-start gap-4 border-b border-rule px-3 py-4 transition-colors hover:bg-white"
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">{card.company}</span>
          <span className="text-muted">—</span>
          <span>{card.title}</span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-center gap-2 text-[13px] text-muted">
          <span>{card.location ?? "Location not stated"}</span>
          <span aria-hidden>·</span>
          <span>{card.salary ?? "—"}</span>
          <span aria-hidden>·</span>
          <span title={card.posted_at ?? "no date from provider"}>
            posted {postedAge(card.posted_at)}
          </span>
          {card.location_kind === "india" && <Badge>India</Badge>}
          {card.location_kind === "remote" && <Badge>remote</Badge>}
          {card.location_kind === "overseas" && <Badge tone="warn">overseas</Badge>}
          <Badge>{card.source}</Badge>
          {card.description_quality === "thin" && <Badge tone="warn">thin JD</Badge>}
          {card.warning_count > 0 && (
            <Badge tone="warn">
              {card.warning_count} flag{card.warning_count === 1 ? "" : "s"}
            </Badge>
          )}
          {!card.has_pdf && <Badge tone="danger">no PDF</Badge>}
        </div>
      </div>
      <div className="flex items-center gap-3 pt-0.5">
        <ScorePill score={card.match_score} />
        <StatusBadge status={card.status} />
      </div>
    </Link>
  );
}

const EMPTY_COPY: Record<string, string> = {
  queued: "Nothing waiting on you. Run `jobpilot run-pipeline` to find more.",
  approved: "Nothing approved yet.",
  applied: "Nothing marked as applied yet.",
  not_selected:
    "Nothing shortlisted. These are scored matches that fell below the tailoring cut — open one and press Tailor this to pursue it.",
  needs_human: "Nothing needs attention — every tailored resume passed the fact-check.",
  rejected: "Nothing rejected. Anything you reject lands here and can be restored.",
  overseas:
    "No overseas roles yet. Anything outside India and open-remote lands here instead of being dropped.",
};

export default async function QueuePage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string; view?: string }>;
}) {
  const { status = "queued", view } = await searchParams;
  // The overseas view is a location filter across every status, not a status.
  const overseas = view === "overseas";
  const active = overseas ? "overseas" : status === "all" ? "" : status;
  const location = overseas ? "overseas" : PREFERRED_LOCATIONS;

  let cards: QueueCard[] = [];
  let total = 0;
  let counts: StatusCount[] = [];
  let error: string | null = null;
  try {
    const [page, fetchedCounts] = await Promise.all([
      getQueue(overseas ? undefined : active || undefined, location),
      getCounts(),
    ]);
    cards = page.cards;
    total = page.total;
    counts = fetchedCounts;
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not reach the API";
  }

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-[22px] font-semibold tracking-tight">
          {overseas ? "Overseas roles" : "Review queue"}
        </h1>
        <span className="text-[13px] text-muted">
          {total > cards.length
            ? `${cards.length} of ${total} applications`
            : `${cards.length} application${cards.length === 1 ? "" : "s"}`}
        </span>
      </div>

      <Tabs active={active} counts={counts} />

      {overseas && !error && (
        <p className="mt-4 text-[13px] text-muted">
          Roles outside India and open-remote. They are scored and kept, but they do not
          spend the daily tailoring budget — open one and press <em>Tailor this</em> to
          pursue it.
        </p>
      )}

      {error && (
        <p className="mt-4 rounded border border-[#8a2222]/25 bg-removed px-4 py-3 text-[14px]">
          {error}. Is the API running?{" "}
          <code className="font-mono">uv run uvicorn jobpilot_api.main:app --reload</code>
        </p>
      )}

      {!error && cards.length === 0 && (
        <p className="mt-6 text-[14px] text-muted">
          {EMPTY_COPY[active] ?? "Nothing here yet."}
        </p>
      )}

      <div>
        {cards.map((card) => (
          <Row key={card.application_id} card={card} />
        ))}
      </div>

      {total > cards.length && (
        <p className="mt-4 text-[13px] text-muted">
          Showing the first {cards.length} of {total}. Narrow the tab, or clear some of
          the queue, to see the rest.
        </p>
      )}
    </div>
  );
}
