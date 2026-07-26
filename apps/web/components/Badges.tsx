export function Badge({
  children,
  tone = "neutral",
}: {
  children: React.ReactNode;
  tone?: "neutral" | "warn" | "good" | "danger";
}) {
  const tones = {
    neutral: "bg-white text-muted border-rule",
    good: "bg-added text-accent border-accent/25",
    warn: "bg-[#fdf6e3] text-warn border-warn/30",
    danger: "bg-removed text-[#8a2222] border-[#8a2222]/25",
  } as const;
  return (
    <span
      className={`inline-flex items-center rounded border px-1.5 py-0.5 text-[11px] font-medium tracking-wide ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const tone =
    status === "approved" || status === "applied"
      ? "good"
      : status === "needs_human"
        ? "danger"
        : status === "rejected"
          ? "neutral"
          : "neutral";
  return <Badge tone={tone}>{status.replace("_", " ")}</Badge>;
}

export function ScorePill({ score }: { score: number | null }) {
  if (score === null) return <span className="text-muted">—</span>;
  return (
    <span className="font-mono text-[13px] tabular-nums" title="LLM match score">
      {score}
    </span>
  );
}
