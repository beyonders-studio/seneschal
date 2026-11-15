import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import "@/i18n";
import { JobProgress } from "@/components/JobProgress";
import { buildJob } from "@/testing";

describe("JobProgress", () => {
  it("says a queued job is waiting", async () => {
    render(<JobProgress job={buildJob()} />);
    await screen.findByText("Waiting to start");
  });

  it("shows a running job's step and how much of it is done", async () => {
    render(
      <JobProgress job={buildJob({ state: "running", step: "reading", done: 2, total: 8 })} />
    );
    await screen.findByText("Reading the records: 2 of 8");
    expect(screen.getByRole("progressbar").getAttribute("aria-valuenow")).toBe("2");
  });

  it("says why a job failed, or that it did", async () => {
    const { unmount } = render(
      <JobProgress job={buildJob({ state: "failed", error: "JOB_FAILED" })} />
    );
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Something went wrong, and Hoard stopped after trying again. Try again in a moment."
    );
    unmount();
    render(<JobProgress job={buildJob({ state: "failed", error: "SOMETHING_NEW" })} />);
    expect((await screen.findByRole("alert")).textContent).toBe(
      "This didn't finish. Try again in a moment."
    );
  });
});
