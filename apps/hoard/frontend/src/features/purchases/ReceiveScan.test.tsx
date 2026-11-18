import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PurchaseLineRead, PurchaseRead, ScanRead } from "@/api/generated/hoard.schemas";
import { SETTINGS } from "@/features/items/testing";
import { cameraReads, openSignedIn, signedIn } from "@/testing";

const flour = { id: 3, kind: "material" as const, name: "Flour", attributes: {}, archived: false };

const line: PurchaseLineRead = {
  id: 9,
  item: flour,
  sku: "FL-1",
  category_id: 2,
  purchase_quantity: "1.000000",
  tracking_quantity: "500.000000",
  unit_cost: "2.000000",
  landed_cost: "0.004000",
  received_on: null,
  lot: null,
};

const purchase: PurchaseRead = {
  id: 5,
  code: "P-0005",
  dated_on: "2026-11-01",
  supplier_contact_id: null,
  location_id: 1,
  paid: false,
  expected_on: null,
  overdue: false,
  notes: null,
  discount: "0.000000",
  shipping: "0.000000",
  tax: "0.000000",
  total: "2.000000",
  lines: [line],
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
};

const read = (overrides: Partial<ScanRead> = {}): ScanRead => ({
  kind: "gs1",
  text: "",
  gtin: "09506000134352",
  lot: "SUP-881",
  serial: null,
  sscc: null,
  expires_on: "2027-06-30",
  best_before: null,
  produced_on: null,
  count: null,
  items: [],
  lots: [],
  problems: [],
  ...overrides,
});

const LABEL = "(01)09506000134352(10)SUP-881(17)270630";

async function openReceive(scan: ScanRead) {
  cameraReads(LABEL);
  const asked = await openSignedIn("/stock/purchases/5", signedIn(), {
    ...SETTINGS,
    "GET /api/v1/purchases/5": { status: 200, data: purchase },
    "GET /api/v1/contacts": { status: 200, data: [] },
    "GET /api/v1/members": { status: 200, data: [] },
    "POST /api/v1/scans": { status: 200, data: scan },
    "PUT /api/v1/items/3/gtin": { status: 200, data: { gtin: "09506000134352" } },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Receive" }));
  const dialog = await screen.findByRole("dialog", { name: "Receive line" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Scan the label" }));
  return { asked, dialog };
}

describe("Receiving by scanning the label", () => {
  it("fills the lot number and expiry from the label, asked as the line's item", async () => {
    const { asked, dialog } = await openReceive(
      read({ items: [{ ...flour, path: "/stock/materials/3" }] })
    );

    await waitFor(() =>
      expect(within(dialog).getByLabelText("Lot number")).toHaveProperty("value", "SUP-881")
    );
    expect(within(dialog).getByLabelText("Expires on")).toHaveProperty("value", "2027-06-30");
    const scanned = asked.find((config) => config.url === "/api/v1/scans");
    expect(JSON.parse(scanned?.data)).toEqual({ text: LABEL, item_id: 3 });
  });

  it("offers to remember a GTIN no item has yet as the line's item's", async () => {
    const { asked } = await openReceive(read({ problems: ["unknown_gtin"] }));

    fireEvent.click(await screen.findByRole("button", { name: "Remember it" }));

    await screen.findByText("09506000134352 is now Flour's GTIN.");
    const kept = asked.find((config) => config.url === "/api/v1/items/3/gtin");
    expect(JSON.parse(kept?.data)).toEqual({ gtin: "09506000134352" });
  });

  it("refuses a label of another item, and fills nothing", async () => {
    const { dialog } = await openReceive(read({ problems: ["item_mismatch"] }));

    await screen.findByText("That label is for another item than Flour.");
    expect(within(dialog).getByLabelText("Lot number")).toHaveProperty("value", "");
  });
});
