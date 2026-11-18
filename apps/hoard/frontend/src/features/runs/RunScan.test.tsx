import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunLineRead, RunRead, ScanRead } from "@/api/generated/hoard.schemas";
import { SETTINGS } from "@/features/items/testing";
import { cameraReads, openSignedIn, signedIn } from "@/testing";

const flour = { id: 3, kind: "material" as const, name: "Flour", attributes: {}, archived: false };

const line: RunLineRead = {
  id: 9,
  item: flour,
  sku: "FL-1",
  picture_id: null,
  unit_id: 1,
  quantity: "4.000000",
  lot: null,
  assumed: false,
  lots: [{ id: 4, number: "L-1", expires_on: null, balance: "10.000000" }],
};

const run: RunRead = {
  id: 5,
  code: "R-0005",
  item: { id: 8, kind: "base", name: "Dough", attributes: {}, archived: false },
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
  recipe_changed: false,
  recipe_notes: null,
  notes: null,
  labour_minutes: null,
  made_for: null,
  expires_on: null,
  output_lot: null,
  lines: [line],
  shipped: [],
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
};

const scanLot = (lot: Partial<ScanRead["lots"][number]> = {}): ScanRead => ({
  kind: "plain",
  text: "L-1",
  gtin: null,
  lot: null,
  serial: null,
  sscc: null,
  expires_on: null,
  best_before: null,
  produced_on: null,
  count: null,
  items: [],
  lots: [
    {
      id: 4,
      item_id: 3,
      location_id: 1,
      number: "L-1",
      expires_on: null,
      balance: "10.000000",
      on_hold: false,
      loose: false,
      path: "/stock/lots/4",
      ...lot,
    },
  ],
  problems: [],
});

async function scanInto(scan: ScanRead) {
  cameraReads("L-1");
  const asked = await openSignedIn("/production/runs/5", signedIn(), {
    ...SETTINGS,
    "GET /api/v1/members": { status: 200, data: [] },
    "GET /api/v1/documents": { status: 200, data: [] },
    "GET /api/v1/runs/5": { status: 200, data: run },
    "POST /api/v1/scans": { status: 200, data: scan },
    "PATCH /api/v1/runs/5/lines/9": {
      status: 200,
      data: { ...run, lines: [{ ...line, lot: line.lots[0] }] },
    },
  });
  fireEvent.click(await screen.findByRole("button", { name: "Scan an input lot" }));
  return asked;
}

describe("Scanning a run's input lots", () => {
  it("sets the scanned lot on its item's input line", async () => {
    const asked = await scanInto(scanLot());

    await screen.findByText("Scanned L-1 for Flour.");
    const scanned = asked.find((config) => config.url === "/api/v1/scans");
    expect(JSON.parse(scanned?.data)).toEqual({ text: "L-1", location_id: 1 });
    const set = asked.find((config) => config.url === "/api/v1/runs/5/lines/9");
    expect(JSON.parse(set?.data)).toEqual({ lot_id: 4 });
  });

  it("refuses a lot on hold", async () => {
    const asked = await scanInto(scanLot({ on_hold: true }));

    await screen.findByText("L-1 is on hold, so the run can't use it.");
    await waitFor(() =>
      expect(asked.some((config) => config.url === "/api/v1/runs/5/lines/9")).toBe(false)
    );
  });

  it("says so when the run doesn't use the scanned item", async () => {
    await scanInto(scanLot({ item_id: 99 }));

    await screen.findByText("This run doesn't use that item.");
  });
});
