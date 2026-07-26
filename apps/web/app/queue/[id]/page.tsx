import Link from "next/link";
import { notFound } from "next/navigation";
import { Actions } from "@/components/Actions";
import { Badge, ScorePill, StatusBadge } from "@/components/Badges";
import { DiffBullet } from "@/components/Diff";
import { getCard } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function CardPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let card;
  try {
    card = await getCard(Number(id));
  } catch {
    notFound();
  }

  return (
    <div className="space-y-8">
      <div>
        <Link href="/queue" className="text-[13px] text-muted hover:underline">
          ← Queue
        </Link>
        <div className="mt-3 flex flex-wrap items-baseline justify-between gap-3">
          <h1 className="text-[22px] font-semibold tracking-tight">
            {card.company} — {card.title}
          </h1>
          <div className="flex items-center gap-3">
            <ScorePill score={card.match_score} />
            <StatusBadge status={card.status} />
          </div>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[13px] text-muted">
          <span>{card.location ?? "Location not stated"}</span>
          <span aria-hidden>·</span>
          <span>{card.salary ?? "Salary not disclosed"}</span>
          <Badge>{card.source}</Badge>
          {card.description_quality === "thin" && <Badge tone="warn">thin JD</Badge>}
          {card.seniority_fit && <Badge>{card.seniority_fit} fit</Badge>}
        </div>
      </div>

      <Actions
        applicationId={card.application_id}
        status={card.status}
        whitelistPassed={card.whitelist_passed}
        applyUrl={card.apply_url}
        hasPdf={card.has_pdf}
      />

      {card.rejections.length > 0 && (
        <section className="rounded border border-[#8a2222]/25 bg-removed p-4">
          <h2 className="text-[14px] font-semibold">
            Fact-check failed after {card.attempts} attempts
          </h2>
          <ul className="mt-2 space-y-1.5 text-[13px]">
            {card.rejections.map((note, i) => (
              <li key={i}>
                <code className="font-mono text-[12px]">{note.rule}</code> — {note.detail}{" "}
                <span className="text-muted">(found: {note.evidence})</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {card.warnings.length > 0 && (
        <section className="rounded border border-warn/30 bg-[#fdf6e3] p-4">
          <h2 className="text-[14px] font-semibold">Flagged for your review</h2>
          <ul className="mt-2 space-y-1.5 text-[13px]">
            {card.warnings.map((note, i) => (
              <li key={i}>
                <code className="font-mono text-[12px]">{note.rule}</code> — {note.detail}
              </li>
            ))}
          </ul>
        </section>
      )}

      {card.rationale && (
        <section>
          <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">
            Why this scored {card.match_score}
          </h2>
          <p className="text-[14px] leading-relaxed">{card.rationale}</p>
          {card.keyword_gaps.length > 0 && (
            <p className="mt-2 text-[13px] text-muted">
              Gaps: {card.keyword_gaps.join(", ")}
            </p>
          )}
        </section>
      )}

      <section>
        <h2 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">
          Summary
        </h2>
        <p className="text-[15px] leading-relaxed">{card.summary || "—"}</p>
      </section>

      <section>
        <h2 className="mb-1 text-[13px] font-semibold uppercase tracking-wide text-muted">
          What changed
        </h2>
        <p className="mb-3 text-[13px] text-muted">
          Employer, title, and dates are always taken from your confirmed facts, never
          from the model.
        </p>
        <div className="border-t border-rule">
          {card.diffs.length === 0 ? (
            <p className="py-4 text-[14px] text-muted">No bullets were rewritten.</p>
          ) : (
            card.diffs.map((diff, i) => <DiffBullet key={i} diff={diff} />)
          )}
        </div>
      </section>

      <details className="rounded border border-rule bg-white/60">
        <summary className="cursor-pointer px-4 py-3 text-[13px] font-medium">
          Job description
        </summary>
        <pre className="whitespace-pre-wrap px-4 pb-4 text-[13px] leading-relaxed text-muted">
          {card.description}
        </pre>
      </details>
    </div>
  );
}
