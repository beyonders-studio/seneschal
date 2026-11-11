import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ReportReorderRow, ReportValuation } from "@/api/generated/hoard.schemas";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const reorder: ReportReorderRow[] = [
  {
    kind: "material",
    id: 3,
    name: "Flour",
    unit: "kg",
    on_hand: "2.500000",
    low_stock_limit: "5.000000",
  },
];

const valuation: ReportValuation = {
  currency: "EUR",
  items: [
    {
      item_id: 3,
      kind: "material",
      name: "Flour",
      attributes: {},
      on_hand: "2.500000",
      average_cost: "4.000000",
      value: "10.000000",
    },
  ],
  total: "10.000000",
};

const answers = {
  "GET /api/v1/settings": { status: 200, data: { currency: "EUR" } },
  "GET /api/v1/reports/reorder": { status: 200, data: reorder },
  "GET /api/v1/reports/valuation": { status: 200, data: valuation },
};

describe("ReportsPage", () => {
  it("shows what to reorder, what stock is worth, and every kind to download", async () => {
    await openSignedIn(
      "/reports",
      signedIn({ page: "reports", permissions: ["use", SEE_COSTS] }),
      answers
    );

    const flour = await screen.findByRole("link", { name: "Flour" });
    expect(flour.getAttribute("href")).toBe("/stock/materials/3");
    expect(screen.getByText("2.5 kg")).toBeTruthy();
    expect(screen.getByText("5 kg")).toBeTruthy();

    await screen.findByText("Stock value");
    expect(await screen.findAllByText("€10.00")).toHaveLength(2);
    expect(screen.getByText("€4.00")).toBeTruthy();

    const download = screen.getByRole("link", { name: "Download Purchase lines as JSON" });
    expect(download.getAttribute("href")).toBe("/api/v1/export/purchase_lines?format=json");
  });

  it("shows no stock value to a member who doesn't see costs, and never asks for it", async () => {
    const asked = await openSignedIn("/reports", signedIn({ page: "reports" }), answers);

    await screen.findByRole("link", { name: "Flour" });
    expect(screen.queryByText("Stock value")).toBeNull();
    expect(asked.map((request) => request.url)).not.toContain("/api/v1/reports/valuation");
  });
});
