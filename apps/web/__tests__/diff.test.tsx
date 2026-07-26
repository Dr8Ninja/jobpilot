import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DiffBullet } from "@/components/Diff";
import { Badge, StatusBadge } from "@/components/Badges";
import type { BulletDiff } from "@/lib/api";

const diff: BulletDiff = {
  employment_index: 0,
  company: "Acme Corp",
  original: "Built REST endpoints for the billing service.",
  rewritten: "Built and shipped REST endpoints for billing, in Python.",
  skills_referenced: ["Python"],
  changed: true,
};

describe("DiffBullet", () => {
  it("shows both sides so approval is informed", () => {
    render(<DiffBullet diff={diff} />);
    expect(screen.getByText("Original")).toBeDefined();
    expect(screen.getByText("Rewritten")).toBeDefined();
  });

  it("attributes the bullet to the canonical employer", () => {
    render(<DiffBullet diff={diff} />);
    expect(screen.getByText("Acme Corp")).toBeDefined();
  });

  it("surfaces which skills the rewrite leans on", () => {
    render(<DiffBullet diff={diff} />);
    expect(screen.getByText("Python")).toBeDefined();
  });

  it("marks an unchanged bullet rather than faking a diff", () => {
    render(
      <DiffBullet
        diff={{ ...diff, rewritten: diff.original, changed: false }}
      />,
    );
    expect(screen.getByText("unchanged")).toBeDefined();
  });

  it("highlights only the words that actually changed", () => {
    const { container } = render(<DiffBullet diff={diff} />);
    const highlighted = container.querySelectorAll("span.bg-added");
    const words = Array.from(highlighted).map((n) => n.textContent?.trim());
    // "Built" and "REST" appear on both sides and must not be marked as new.
    expect(words).not.toContain("Built");
    expect(words).not.toContain("REST");
    expect(words).toContain("shipped");
    expect(words).toContain("Python.");
  });
});

describe("StatusBadge", () => {
  it("renders the status readably", () => {
    render(<StatusBadge status="needs_human" />);
    expect(screen.getByText("needs human")).toBeDefined();
  });

  it("renders a plain badge", () => {
    render(<Badge>aggregator</Badge>);
    expect(screen.getByText("aggregator")).toBeDefined();
  });
});
