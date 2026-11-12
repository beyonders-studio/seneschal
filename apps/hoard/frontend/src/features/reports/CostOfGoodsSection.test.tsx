import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  HoardPropertyRead,
  ReportBalanceRow,
  ReportCostOfGoods,
} from "@/api/generated/hoard.schemas";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const costOfGoods: ReportCostOfGoods = {
  currency: "EUR",
  opening: "20.000000",
  starting: "0.000000",
  purchases: "40.000000",
  personal_use: "6.000000",
  closing: "45.000000",
  cost_of_goods: "9.000000",
  shipped: "4.000000",
  uncosted_runs: 2,
};

const row = (fields: Partial<ReportBalanceRow>): ReportBalanceRow => ({
  item_id: 3,
  kind: "material",
  name: "Tea",
  attributes: {},
  unit: "kg",
  option: null,
  opening: "1.000000",
  received: "8.000000",
  made: "0.000000",
  used: "0.000000",
  sold: "3.000000",
  lost: "0.500000",
  closing: "5.500000",
  ...fields,
});

const fairTrade: HoardPropertyRead = {
  id: 7,
  kind: "lot",
  name: "Fair trade",
  type: "select",
  options: [
    { value: "certified", label: "Certified", color: null },
    { value: "not_certified", label: "Not certified", color: null },
  ],
  currency: null,
  counter: null,
};

const answers = (balance: ReportBalanceRow[]) => ({
  "GET /api/v1/settings": { status: 200, data: { currency: "EUR" } },
  "GET /api/v1/reports/reorder": { status: 200, data: [] },
  "GET /api/v1/reports/valuation": {
    status: 200,
    data: { currency: "EUR", items: [], total: "0" },
  },
  "GET /api/v1/reports/cost-of-goods": { status: 200, data: costOfGoods },
  "GET /api/v1/reports/mass-balance": { status: 200, data: balance },
  "GET /api/v1/properties": { status: 200, data: [fairTrade] },
});

// jsdom has no layout, and the select scrolls its chosen option into view.
Element.prototype.scrollIntoView ??= () => {};

async function choose(name: string, option: string) {
  fireEvent.keyDown(await screen.findByRole("combobox", { name }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

describe("CostOfGoodsSection", () => {
  it("shows what the goods cost this year so far, and warns of uncosted runs", async () => {
    const asked = await openSignedIn(
      "/reports",
      signedIn({ page: "reports", permissions: ["use", SEE_COSTS] }),
      answers([row({})])
    );

    await screen.findByText("Less stock taken for personal use");
    expect(screen.getByText("€9.00")).toBeTruthy();
    expect(screen.getByText("€45.00")).toBeTruthy();
    expect(screen.getByText(/2 production runs in this period are uncosted/)).toBeTruthy();
    const year = String(new Date().getFullYear());
    const params = asked.find((request) => request.url === "/api/v1/reports/cost-of-goods")?.params;
    expect(params.start).toBe(`${year}-01-01`);
  });

  it("shows the mass balance to every member, but not what the goods cost", async () => {
    const asked = await openSignedIn("/reports", signedIn({ page: "reports" }), answers([row({})]));

    const tea = await screen.findByRole("row", { name: /Tea/ });
    expect(within(tea).getByText("5.5")).toBeTruthy();
    expect(within(tea).getByText("kg")).toBeTruthy();
    expect(screen.queryByText("Cost of goods")).toBeNull();
    expect(asked.map((request) => request.url)).not.toContain("/api/v1/reports/cost-of-goods");
  });

  it("splits the balance by a lot property, and shows one option's volume", async () => {
    const asked = await openSignedIn(
      "/reports",
      signedIn({ page: "reports" }),
      answers([row({ option: "certified" }), row({ option: null, received: "2.000000" })])
    );
    await screen.findAllByRole("row", { name: /Tea/ });

    await choose("Split by", "Fair trade");
    expect(await screen.findByRole("cell", { name: "Certified" })).toBeTruthy();
    expect(screen.getByRole("cell", { name: "None recorded" })).toBeTruthy();
    const split = asked.filter((request) => request.url === "/api/v1/reports/mass-balance");
    expect(split.at(-1)?.params.split_by).toBe(7);

    expect(
      screen.getByText("Choose a lot property, and the option that means certified.")
    ).toBeTruthy();
    await choose("Property", "Fair trade");
    await choose("Option", "Certified");
    const volume = await screen.findAllByRole("row", { name: /Tea/ });
    // The balance split by its options, then the volume of the one chosen.
    expect(volume).toHaveLength(3);
    expect(within(volume[2]).getByText("8")).toBeTruthy();
  });
});
