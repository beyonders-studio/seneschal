import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunLineRead, RunRead } from "@/api/generated/hoard.schemas";
import { openSignedIn, signedIn } from "@/testing";

const lot = { id: 4, number: "L-1", expires_on: null, balance: "10.000000" };

const line = (overrides: Partial<RunLineRead> = {}): RunLineRead => ({
  id: 9,
  item: { id: 3, kind: "material", name: "Flour", attributes: {} },
  quantity: "4.000000",
  lot: null,
  assumed: false,
  lots: [lot],
  ...overrides,
});

const run = (overrides: Partial<RunRead> = {}): RunRead => ({
  id: 5,
  code: "R-0005",
  item: { id: 8, kind: "base", name: "Dough", attributes: {} },
  status: "in_progress",
  location_id: 1,
  initiative_id: 7,
  planned_batches: "2.000000",
  planned_quantity: "8.000000",
  actual_quantity: null,
  assignee_ref: null,
  started_on: "2026-11-02",
  deadline_on: null,
  completed_on: null,
  uncosted: false,
  recipe_id: 2,
  labour_minutes: null,
  expires_on: null,
  output_lot: null,
  lines: [line()],
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
  ...overrides,
});

const ANSWERS = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/members": { status: 200, data: [] },
};

describe("RunPage", () => {
  it("completes a run with what it made", async () => {
    const asked = await openSignedIn(
      "/production/runs/5",
      signedIn({ page: "production", permissions: ["use", "costs"] }),
      {
        ...ANSWERS,
        "GET /api/v1/runs/5": { status: 200, data: run() },
        "POST /api/v1/runs/5/complete": {
          status: 200,
          data: run({
            status: "completed",
            actual_quantity: "7.000000",
            completed_on: "2026-11-03",
            cost: "8.000000",
            unit_cost: "1.142857",
            output_lot: { id: 6, number: "R-0005", expires_on: null, balance: "7.000000" },
            lines: [
              line({ lot, assumed: true, lots: [], unit_cost: "2.000000", cost: "8.000000" }),
            ],
          }),
        },
      }
    );

    await screen.findByRole("heading", { name: "Run R-0005" });
    fireEvent.click(screen.getByRole("button", { name: "Complete" }));
    const dialog = await screen.findByRole("dialog", { name: "Complete run R-0005" });
    fireEvent.change(within(dialog).getByLabelText("Made"), { target: { value: "7" } });
    fireEvent.change(within(dialog).getByLabelText("Completed on"), {
      target: { value: "2026-11-03" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Complete" }));

    await screen.findByText("Assumed");
    expect(screen.getByText("$8.00", { selector: "dd" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reopen" })).toBeTruthy();
    const completing = asked.find((request) => request.url?.endsWith("/complete"));
    expect(JSON.parse(completing?.data)).toEqual({
      actual_quantity: "7",
      completed_on: "2026-11-03",
    });
  });

  it("warns of an uncosted run, with no costs for who does not see them", async () => {
    await openSignedIn("/production/runs/5", signedIn({ page: "production" }), {
      ...ANSWERS,
      "GET /api/v1/runs/5": {
        status: 200,
        data: run({ status: "completed", uncosted: true, actual_quantity: "8.000000" }),
      },
    });

    await screen.findByText("This run is uncosted");
    expect(screen.queryByText("Cost of what it consumed")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Unit cost" })).toBeNull();
  });
});
