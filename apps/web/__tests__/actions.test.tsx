import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Actions } from "@/components/Actions";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh }),
}));

function json(status: number, body: unknown) {
  return { ok: status < 400, status, json: async () => body } as Response;
}

const shortlisted = {
  applicationId: 7,
  status: "not_selected",
  whitelistPassed: false,
  applyUrl: "https://example.invalid/apply",
  hasPdf: false,
};

afterEach(() => {
  vi.restoreAllMocks();
  refresh.mockClear();
});

describe("Actions — tailoring is queued, not awaited", () => {
  it("polls the run until it finishes, then refreshes", async () => {
    // Tailoring is up to three attempts at 180s. The request returns a run id
    // immediately; the page has to follow the run rather than assume it is done.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(202, { application_id: 7, run_id: 42, status: "pending" }))
      .mockResolvedValueOnce(json(200, { id: 42, status: "running" }))
      .mockResolvedValueOnce(json(200, { id: 42, status: "succeeded" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Actions {...shortlisted} />);
    await userEvent.click(screen.getByRole("button", { name: /tailor this/i }));

    await waitFor(() => expect(refresh).toHaveBeenCalled(), { timeout: 3000 });

    expect(fetchMock.mock.calls[0][0]).toBe("/api/queue/7/tailor");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/v1/runs/42");
  });

  it("surfaces the reason a run failed instead of silently refreshing", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(202, { application_id: 7, run_id: 43, status: "pending" }))
      .mockResolvedValueOnce(
        json(200, { id: 43, status: "failed", error: "provider timed out" }),
      );
    vi.stubGlobal("fetch", fetchMock);

    render(<Actions {...shortlisted} />);
    await userEvent.click(screen.getByRole("button", { name: /tailor this/i }));

    expect(await screen.findByText(/provider timed out/i)).toBeDefined();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("reports a queue that cannot be reached", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json(503, { detail: "Could not reach the task queue." }));
    vi.stubGlobal("fetch", fetchMock);

    render(<Actions {...shortlisted} />);
    await userEvent.click(screen.getByRole("button", { name: /tailor this/i }));

    expect(await screen.findByText(/could not reach the task queue/i)).toBeDefined();
  });
});

describe("Actions — the gate still governs the buttons", () => {
  it("refuses to offer approval for a run that failed the fact-check", () => {
    render(<Actions {...shortlisted} status="queued" />);
    const approve = screen.getByRole("button", { name: "Approve" }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
    expect(screen.getByText(/failed the fact-check/i)).toBeDefined();
  });

  it("offers no PDF link when the gate did not pass", () => {
    render(<Actions {...shortlisted} status="queued" hasPdf />);
    expect(screen.queryByText("Tailored PDF")).toBeNull();
  });

  it("links the PDF once the gate has passed", () => {
    render(<Actions {...shortlisted} status="queued" hasPdf whitelistPassed />);
    expect(screen.getByText("Tailored PDF")).toBeDefined();
  });
});
