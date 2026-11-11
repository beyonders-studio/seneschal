import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StocktakeRead, StocktakeRowRead } from "@/api/generated/hoard.schemas";
import { openSignedIn, signedIn } from "@/testing";

const stocktake = (row: Partial<StocktakeRowRead>, progress: Partial<StocktakeRead>) => ({
  id: 3,
  kind: "material",
  location_id: 1,
  category_id: null,
  sample_size: null,
  dated_on: "2026-11-05",
  notes: null,
  row_count: 1,
  confirmed_count: 0,
  accuracy: null,
  finished: false,
  created_at: "2026-11-05T09:00:00Z",
  created_by: "member-ref",
  rows: [
    {
      id: 11,
      item: { id: 4, kind: "material", name: "Flour", attributes: {} },
      on_hand: "10.000000",
      expected: null,
      counted: null,
      ...row,
    },
  ],
  ...progress,
});

describe("StocktakePage", () => {
  it("confirms a row as counted, to the stocktake's accuracy", async () => {
    const asked = await openSignedIn("/stock/stocktakes/3", signedIn(), {
      "GET /api/v1/stocktakes/3": { status: 200, data: stocktake({}, {}) },
      "POST /api/v1/stocktakes/3/rows/11/confirm": {
        status: 200,
        data: stocktake(
          { on_hand: "8.000000", expected: "10.000000", counted: "8.000000" },
          { confirmed_count: 1, accuracy: "0.0000", finished: true }
        ),
      },
      "GET /api/v1/members": { status: 200, data: [] },
      "GET /api/v1/adjustments/options": {
        status: 200,
        data: { items: [], locations: [], reasons: [], categories: [] },
      },
    });

    await screen.findByText(/0 of 1 counted/);
    fireEvent.change(screen.getByLabelText("Counted of Flour"), { target: { value: "8" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm" }));

    await screen.findByRole("button", { name: "Recount" });
    screen.getByText(/1 of 1 counted · 0% accurate/);
    screen.getByText("Finished");
    screen.getByRole("cell", { name: "-2" });
    const confirm = asked.find((request) => request.url?.endsWith("/confirm"));
    expect(JSON.parse(confirm?.data)).toEqual({ counted: "8" });
  });
});
