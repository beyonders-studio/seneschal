import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { AdjustmentRead } from "@/api/generated/hoard.schemas";
import { openSignedIn } from "@/testing";

const adjustment = (overrides: Partial<AdjustmentRead>): AdjustmentRead => ({
  id: 1,
  item: { id: 3, kind: "material", name: "Flour", attributes: {} },
  location_id: 1,
  lot_id: null,
  kind: "manual",
  dated_on: "2026-11-07",
  change: "-1.000000",
  reason_id: null,
  notes: null,
  purchase_id: null,
  stocktake_id: null,
  order_id: null,
  created_at: "2026-11-07T09:00:00Z",
  created_by: null,
  updated_at: "2026-11-07T09:00:00Z",
  updated_by: null,
  ...overrides,
});

describe("AdjustmentsPage", () => {
  it("changes a damaged return's write-off only through its order", async () => {
    await openSignedIn("/stock/adjustments", undefined, {
      "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
      "GET /api/v1/adjustments": {
        status: 200,
        data: {
          items: [
            adjustment({}),
            adjustment({
              id: 2,
              item: { id: 4, kind: "variation", name: "Chai", attributes: {} },
              order_id: 5,
            }),
          ],
          total: 2,
        },
      },
    });

    await screen.findByRole("button", { name: "Actions for Flour" });
    expect(screen.queryByRole("button", { name: "Actions for Chai" })).toBeNull();
    const links = screen.getAllByRole("link").map((link) => link.getAttribute("href"));
    expect(links).toContain("/orders/5");
  });
});
