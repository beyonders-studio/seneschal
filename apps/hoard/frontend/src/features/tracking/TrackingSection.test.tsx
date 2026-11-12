import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { openSignedIn, signedIn } from "@/testing";

const WAITING = {
  runs: 1,
  purchases: 3,
  waiting: 2,
  failing: 2,
  retry_at: "2026-11-12T10:30:00Z",
};

describe("TrackingSection", () => {
  it("shows what is tracked and what waits, without sending it for a member", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings" }), {
      "GET /api/v1/tracking": { status: 200, data: WAITING },
    });

    await screen.findByText("Tasks track 1 production run and 3 purchases.");
    expect(
      screen.getByText(/2 events are waiting to be sent to Initiative\. Initiative couldn't/)
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
  });

  it("lets an admin send the waiting events now", async () => {
    const asked = await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      "GET /api/v1/tracking": { status: 200, data: WAITING },
      "POST /api/v1/tracking/send": {
        status: 200,
        data: { ...WAITING, waiting: 0, failing: 0, retry_at: null },
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Send now" }));

    await waitFor(() =>
      expect(screen.getAllByText("Every event has reached Initiative.").length).toBeGreaterThan(0)
    );
    expect(asked.some((request) => request.url === "/api/v1/tracking/send")).toBe(true);
    expect(screen.queryByRole("button", { name: "Send now" })).toBeNull();
  });
});
