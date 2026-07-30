import Link from "next/link";
import { getSkillGaps, type SkillGapRow } from "@/lib/api";

export const dynamic = "force-dynamic";

/** What to learn next, and who is asking for it.
 *
 * Built from the gaps the scoring stage already records per job. It never
 * changes the resume — a gap here is a thing to go and learn, and the
 * fact-check still rejects any skill that is not genuinely yours. */
export default async function SkillsPage({
  searchParams,
}: {
  searchParams: Promise<{ min?: string }>;
}) {
  const { min } = await searchParams;
  const minJobs = Number(min ?? "2") || 1;

  let gaps: SkillGapRow[] = [];
  let error: string | null = null;
  try {
    gaps = await getSkillGaps(minJobs);
  } catch (e) {
    error = e instanceof Error ? e.message : "Could not reach the API";
  }

  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between">
        <h1 className="text-[22px] font-semibold tracking-tight">Skills to learn</h1>
        <Link href="/queue" className="text-[13px] text-muted hover:text-ink">
          ← Back to queue
        </Link>
      </div>
      <p className="mb-5 max-w-[62ch] text-[13px] text-muted">
        Every term a job description asked for that your resume does not cover, ranked by
        how many jobs wanted it. Nothing here is ever added to your resume — that is the
        point of the fact-check. This is what to go and learn, and who is asking.
      </p>

      <div className="mb-4 flex gap-1 text-[13px]">
        {[1, 2, 3, 5].map((n) => (
          <Link
            key={n}
            href={`/skills?min=${n}`}
            className={`rounded border px-2.5 py-1 transition-colors ${
              minJobs === n
                ? "border-accent text-accent"
                : "border-rule text-muted hover:text-ink"
            }`}
          >
            {n}+ job{n === 1 ? "" : "s"}
          </Link>
        ))}
      </div>

      {error && (
        <p className="rounded border border-[#8a2222]/25 bg-removed px-4 py-3 text-[14px]">
          {error}. Is the API running?
        </p>
      )}

      {!error && gaps.length === 0 && (
        <p className="text-[14px] text-muted">
          Nothing yet — score some jobs first with <code className="font-mono">jobpilot run-pipeline</code>,
          or lower the threshold above.
        </p>
      )}

      <ol className="space-y-0">
        {gaps.map((gap, index) => (
          <li
            key={gap.skill}
            className="grid grid-cols-[2rem_1fr_auto] items-start gap-3 border-b border-rule px-1 py-3.5"
          >
            <span className="pt-0.5 text-right tabular-nums text-[13px] text-muted">
              {index + 1}
            </span>
            <div className="min-w-0">
              <div className="font-medium">{gap.skill}</div>
              <div className="mt-1 text-[13px] text-muted">
                {gap.companies.slice(0, 6).join(", ")}
                {gap.companies.length > 6 && ` +${gap.companies.length - 6} more`}
              </div>
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-[12px] text-muted">
                {gap.examples.slice(0, 3).map((example) => (
                  <span key={`${example.job_id}`}>
                    {example.company} · {example.title}
                  </span>
                ))}
              </div>
            </div>
            <span className="whitespace-nowrap pt-0.5 text-[13px] tabular-nums text-muted">
              {gap.job_count} job{gap.job_count === 1 ? "" : "s"}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
