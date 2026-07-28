import Link from "next/link";
import { Badge, ScorePill, StatusBadge } from "@/components/Badges";
import { Tabs } from "@/components/Tabs";
import {
  getCounts,
  getQueue,
  postedAge,
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
  needs_human: "Nothing needs attention — every tailored resume passed the fact-check.",
  rejected: "Nothing rejected. Anything you reject lands here and can be restored.",
};

export default async function QueuePage({
  searchParams,
}: {
  searchParams: Promise<{ status?: string }>;
}) {
  const { status = "queued" } = await searchParams;
  const active = status === "all" ? "" : status;

  let cards: QueueCard[] = [];
  let counts: StatusCount[] = [];
  let error: string | null = null;
  try {
    [cards, counts] = await Promise.all([
      getQueue(active || undefined),
      getCounts(),
    ]);
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not reach the API";
  }

  return (
    <div>
      <div className="mb-4 flex items-baseline justify-between">
        <h1 className="text-[22px] font-semibold tracking-tight">Review queue</h1>
        <span className="text-[13px] text-muted">
          {cards.length} application{cards.length === 1 ? "" : "s"}
        </span>
      </div>

      <Tabs active={active} counts={counts} />

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
    </div>
  );
}
