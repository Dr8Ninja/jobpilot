"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

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
