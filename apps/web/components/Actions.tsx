"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

const POLL_INTERVAL_MS = 1_500;
/** Worst case is `max_tailoring_attempts` x `llm_timeout_seconds`, plus slack. */
const POLL_TIMEOUT_MS = 15 * 60 * 1_000;

/** Follow a background run to its end. Resolves when it succeeds, throws when
 * it does not — a run that failed has a reason, and the reason is the point. */
async function waitForRun(runId: number): Promise<void> {
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  for (;;) {
    const response = await fetch(`/api/v1/runs/${runId}`, { cache: "no-store" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({}));
      throw new Error(body.detail ?? `Could not read run ${runId}`);
    }
    const run = await response.json();
    if (run.status === "succeeded") return;
    if (run.status === "failed") {
      throw new Error(run.error ?? "The run failed.");
    }
    if (Date.now() > deadline) {
      throw new Error(`Run ${runId} is still going. Check back shortly.`);
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
  }
}

export function Actions({
  applicationId,
  status,
  whitelistPassed,
  applyUrl,
  hasPdf,
}: {
  applicationId: number;
  status: string;
  whitelistPassed: boolean;
  applyUrl: string;
  hasPdf: boolean;
}) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function act(action: "approve" | "reject" | "applied" | "restore" | "tailor") {
    setBusy(true);
    setError(null);
    try {
      const response = await fetch(`/api/queue/${applicationId}/${action}`, {
        method: "POST",
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail ?? `Failed (${response.status})`);
      }
      // Tailoring is queued, not done: up to three LLM attempts at 180s each is
      // far longer than this request could stay open, so the server hands back a
      // run to follow instead of a result.
      if (action === "tailor") {
        const { run_id: runId } = await response.json();
        await waitForRun(runId);
      }
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  }

  const base =
    "rounded border px-3 py-1.5 text-[13px] font-medium transition-colors disabled:opacity-40";

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        {status === "not_selected" && (
          <button
            className={`${base} border-accent bg-accent text-white hover:bg-[#18493d]`}
            disabled={busy}
            onClick={() => act("tailor")}
            title="Generate a tailored resume for this role and move it to the review queue"
          >
            {busy ? "Tailoring…" : "Tailor this"}
          </button>
        )}
        <button
          className={`${base} border-accent bg-accent text-white hover:bg-[#18493d] ${
            status === "not_selected" ? "hidden" : ""
          }`}
          disabled={busy || !whitelistPassed || status === "approved"}
          onClick={() => act("approve")}
          title={
            whitelistPassed
              ? "Mark ready to apply"
              : "Blocked: this output did not pass the fact-check"
          }
        >
          Approve
        </button>
        {status === "rejected" || status === "needs_human" ? (
          <button
            className={`${base} border-rule bg-white hover:bg-[#f2f0ec]`}
            disabled={busy}
            onClick={() => act("restore")}
            title="Move this back into the review queue"
          >
            Restore to queue
          </button>
        ) : (
          <button
            className={`${base} border-rule bg-white hover:bg-[#f2f0ec]`}
            disabled={busy}
            onClick={() => act("reject")}
            title="Moves to the Rejected tab. Nothing is deleted — you can restore it."
          >
            Reject
          </button>
        )}

        {hasPdf && whitelistPassed && (
          <a
            className={`${base} border-rule bg-white hover:bg-[#f2f0ec]`}
            href={`/api/queue/${applicationId}/pdf`}
            target="_blank"
            rel="noreferrer"
          >
            Tailored PDF
          </a>
        )}

        <a
          className={`${base} border-rule bg-white hover:bg-[#f2f0ec]`}
          href={applyUrl}
          target="_blank"
          rel="noreferrer"
        >
          Open application ↗
        </a>

        <button
          className={`${base} border-rule bg-white hover:bg-[#f2f0ec]`}
          disabled={busy || !whitelistPassed || status === "applied"}
          onClick={() => act("applied")}
          title="Phase 0 apply is manual — record it here once you have submitted"
        >
          I applied
        </button>
      </div>

      {!whitelistPassed && (
        <p className="text-[13px] text-[#8a2222]">
          Approval is blocked: this tailoring run failed the fact-check. Review the
          rejections below.
        </p>
      )}
      {error && <p className="text-[13px] text-[#8a2222]">{error}</p>}
    </div>
  );
}
