import type { BulletDiff } from "@/lib/api";

/** Word-level diff. Enough to make "what did the AI change?" answerable at a glance. */
function tokenize(text: string): string[] {
  return text.split(/(\s+)/);
}

function markup(original: string, rewritten: string) {
  const a = tokenize(original);
  const b = tokenize(rewritten);
  const inA = new Set(a.map((t) => t.trim().toLowerCase()).filter(Boolean));
  const inB = new Set(b.map((t) => t.trim().toLowerCase()).filter(Boolean));
  return {
    removed: a.map((t) => ({ text: t, changed: !!t.trim() && !inB.has(t.trim().toLowerCase()) })),
    added: b.map((t) => ({ text: t, changed: !!t.trim() && !inA.has(t.trim().toLowerCase()) })),
  };
}

export function DiffBullet({ diff }: { diff: BulletDiff }) {
  const { removed, added } = markup(diff.original, diff.rewritten);

  return (
    <div className="border-b border-rule py-4 last:border-b-0">
      <div className="mb-2 flex flex-wrap items-center gap-2 text-[12px] text-muted">
        <span>{diff.company}</span>
        {diff.skills_referenced.map((skill) => (
          <span
            key={skill}
            className="rounded border border-accent/25 bg-added px-1.5 py-0.5 text-accent"
          >
            {skill}
          </span>
        ))}
        {!diff.changed && <span className="italic">unchanged</span>}
      </div>

      <div className="grid gap-2 md:grid-cols-2">
        <div className="rounded border border-rule bg-white/60 p-3 text-[14px] leading-relaxed">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-muted">
            Original
          </div>
          <p>
            {removed.map((token, i) => (
              <span key={i} className={token.changed ? "bg-removed line-through decoration-[#8a2222]/40" : ""}>
                {token.text}
              </span>
            ))}
          </p>
        </div>
        <div className="rounded border border-accent/20 bg-white p-3 text-[14px] leading-relaxed">
          <div className="mb-1 text-[11px] uppercase tracking-wide text-accent">
            Rewritten
          </div>
          <p>
            {added.map((token, i) => (
              <span key={i} className={token.changed ? "bg-added font-medium" : ""}>
                {token.text}
              </span>
            ))}
          </p>
        </div>
      </div>
    </div>
  );
}
