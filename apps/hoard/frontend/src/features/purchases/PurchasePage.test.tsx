import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PurchaseLineRead, PurchaseRead } from "@/api/generated/hoard.schemas";
import { openSignedIn, signedIn } from "@/testing";

const line = (overrides: Partial<PurchaseLineRead> = {}): PurchaseLineRead => ({
  id: 9,
  material: { id: 3, kind: "material", name: "Flour", attributes: {} },
  purchase_quantity: "10.000000",
  tracking_quantity: "10.000000",
  unit_cost: "2.000000",
  landed_cost: "2.000000",
  received: false,
  lot: null,
  ...overrides,
});

const purchase = (lines: PurchaseLineRead[]): PurchaseRead => ({
  id: 5,
  code: "P-0005",
  dated_on: "2026-11-01",
  supplier_contact_id: null,
  location_id: 1,
  paid: false,
  discount: "0.000000",
  shipping: "0.000000",
  tax: "0.000000",
  total: "20.000000",
  lines,
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
});

describe("PurchasePage", () => {
  it("receives a line into a lot", async () => {
    const asked = await openSignedIn(
      "/stock/purchases/5",
      signedIn({ permissions: ["use", "costs"] }),
      {
        "GET /api/v1/purchases/5": { status: 200, data: purchase([line()]) },
        "POST /api/v1/purchases/5/lines/9/receive": {
          status: 200,
          data: purchase([
            line({
              received: true,
              lot: { id: 4, number: "L-7", expires_on: null, notes: null },
            }),
          ]),
        },
        "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
        "GET /api/v1/contacts": { status: 200, data: [] },
        "GET /api/v1/members": { status: 200, data: [] },
        "GET /api/v1/adjustments/options": {
          status: 200,
          data: { items: [], locations: [], reasons: [], categories: [] },
        },
      }
    );

    await screen.findByRole("heading", { name: "Purchase P-0005" });
    await screen.findByText("$20.00");
    fireEvent.click(screen.getByRole("button", { name: "Receive" }));
    const dialog = await screen.findByRole("dialog", { name: "Receive line" });
    fireEvent.change(within(dialog).getByLabelText("Lot number"), { target: { value: "L-7" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Receive" }));

    await screen.findByRole("cell", { name: "L-7" });
    expect(screen.queryByRole("dialog")).toBeNull();
    const receive = asked.find((request) => request.url?.endsWith("/receive"));
    expect(JSON.parse(receive?.data)).toEqual({ number: "L-7" });
  });
});
