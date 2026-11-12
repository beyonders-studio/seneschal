import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { TraceRecallRead } from "@/api/generated/hoard.schemas";
import { SETTINGS } from "@/features/items/testing";
import { traceStep } from "@/features/trace/testing";
import { openSignedIn, signedIn } from "@/testing";

const recall: TraceRecallRead = {
  traces: [
    {
      lot: { id: 3, number: "L-3" },
      item: { id: 5, kind: "material", name: "Oolong", attributes: {} },
      dated_on: "2026-11-01",
      steps: [
        traceStep(),
        traceStep({
          record_id: 9,
          code: "R-9",
          made_lot: { id: 10, number: "R-9" },
          assumed: true,
        }),
        traceStep({
          kind: "shipment",
          record_id: 12,
          code: "O-12",
          order_id: 30,
          lot: { id: 8, number: "R-7" },
          made_lot: null,
          party: { contact_id: 9, name: "Corner Café" },
          level: 2,
        }),
      ],
    },
  ],
};

describe("RecallPage", () => {
  it("recalls the lot it was opened from, with every file to download", async () => {
    const asked = await openSignedIn("/stock/recall?lot=3", signedIn(), {
      ...SETTINGS,
      "GET /api/v1/trace/recall": { status: 200, data: recall },
    });

    const made = (await screen.findByText("Lots made from it")).parentElement as HTMLElement;
    expect(within(made).getByRole("link", { name: "R-7" }).getAttribute("href")).toBe(
      "/stock/lots/8"
    );
    const assumed = within(made).getByRole("link", { name: "R-9" }).parentElement as HTMLElement;
    expect(within(assumed).getByText("May have")).toBeTruthy();

    const shipments = screen.getByText("Shipments").parentElement as HTMLElement;
    expect(within(shipments).getByText("Corner Café")).toBeTruthy();
    expect(within(shipments).getByRole("link", { name: "Order O-12" })).toBeTruthy();

    expect(screen.getByRole("link", { name: "FDA spreadsheet" }).getAttribute("href")).toBe(
      "/api/v1/trace/recall/export?format=fda&lot_id=3"
    );
    expect(screen.getByRole("link", { name: "PDF" }).getAttribute("href")).toBe(
      "/api/v1/trace/recall/export?format=pdf&lot_id=3"
    );
    expect(asked.find((request) => request.url === "/api/v1/trace/recall")?.params).toEqual({
      lot_id: 3,
    });
  });

  it("asks for an item and dates until it is given one", async () => {
    const asked = await openSignedIn("/stock/recall", signedIn(), SETTINGS);

    await screen.findByText("Recall a lot");
    expect(screen.getByRole("button", { name: "Recall" }).hasAttribute("disabled")).toBe(true);
    expect(screen.getByLabelText("Lots dated from")).toBeTruthy();
    expect(asked.map((request) => request.url)).not.toContain("/api/v1/trace/recall");
  });
});
