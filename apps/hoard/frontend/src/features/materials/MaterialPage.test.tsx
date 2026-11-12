import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HoardPropertySummary } from "@/api/generated/hoard.schemas";
import { materialDetail, SETTINGS } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const OOLONG = materialDetail({
  on_hand: "13.000000",
  low_stock_limit: "5.000000",
  purchase_unit_id: 1,
  purchase_conversion: "250.000000",
  average_cost: "2.000000",
  stock: [
    { location_id: 10, quantity: "10.000000" },
    { location_id: 11, quantity: "3.000000" },
  ],
  lots: [
    {
      id: 1,
      location_id: 10,
      number: "L-42",
      dated_on: "2026-11-01",
      expires_on: null,
      notes: null,
      balance: "10.000000",
      properties: [],
    },
  ],
});

const opened = { ...SETTINGS, "GET /api/v1/materials/5": { status: 200, data: OOLONG } };

const ORIGIN: HoardPropertySummary = {
  property_id: 9,
  name: "Origin",
  type: "text",
  options: null,
  currency: null,
  counter: null,
  project_id: null,
  collection_id: null,
  install_id: null,
  key: null,
  value: "Fujian",
};

describe("MaterialPage", () => {
  it("shows a material's stock and lots at each location, and who added it", async () => {
    await openSignedIn("/stock/materials/5", signedIn(), opened);

    await screen.findByRole("heading", { name: "Oolong" });
    const [stock, lots] = screen.getAllByRole("table");
    await within(stock).findByText("Main location");
    within(stock).getByText("10 g");
    within(stock).getByText("Back room");
    within(stock).getByText("3 g");
    within(lots).getByText("L-42");
    screen.getByText("13 g");
    screen.getByText("g of 250 g");
    await screen.findByText(/Added by Ada/);
    expect(screen.queryByText("Average cost")).toBeNull();
  });

  it("shows its average cost to a member who sees costs", async () => {
    await openSignedIn("/stock/materials/5", signedIn({ permissions: ["use", SEE_COSTS] }), opened);

    await screen.findByText("Average cost");
    await screen.findByText("$2.00");
  });

  it("leaves out editing while Hoard is read-only", async () => {
    await openSignedIn("/stock/materials/5", signedIn({ read_only: true }), opened);

    await screen.findByRole("heading", { name: "Oolong" });
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });

  it("shows a material's properties and saves a change to one", async () => {
    const asked = await openSignedIn("/stock/materials/5", signedIn(), {
      ...opened,
      "GET /api/v1/materials/5": { status: 200, data: { ...OOLONG, properties: [ORIGIN] } },
      "PUT /api/v1/properties/values/material/5": {
        status: 200,
        data: [{ ...ORIGIN, value: "Anxi" }],
      },
    });

    const origin = await screen.findByDisplayValue("Fujian");
    screen.getByText("Origin");
    fireEvent.change(origin, { target: { value: "Anxi" } });
    fireEvent.keyDown(origin, { key: "Enter" });

    await waitFor(() => expect(asked.some((request) => request.method === "put")).toBe(true));
    const saved = asked.find((request) => request.method === "put");
    expect(JSON.parse(saved?.data)).toEqual({ values: [{ property_id: 9, value: "Anxi" }] });
  });

  it("offers its layouts to an admin alone", async () => {
    await openSignedIn("/stock/materials/5", signedIn(), opened);

    await screen.findByRole("heading", { name: "Oolong" });
    expect(screen.queryByRole("button", { name: "Layouts" })).toBeNull();
  });

  it("opens the layout editor for an admin", async () => {
    await openSignedIn("/stock/materials/5", signedIn({ admin: true }), opened);

    fireEvent.click(await screen.findByRole("button", { name: "Layouts" }));

    await screen.findByRole("dialog", { name: "Lay out materials" });
  });

  it("finds a member for a person-valued property among the community's", async () => {
    const owner = { ...ORIGIN, property_id: 10, name: "Buyer", type: "user_reference" as const };
    await openSignedIn("/stock/materials/5", signedIn(), {
      ...opened,
      "GET /api/v1/materials/5": {
        status: 200,
        data: { ...OOLONG, properties: [{ ...owner, value: "member-ref" }] },
      },
      "GET /api/v1/members/search": { status: 200, data: [{ member_ref: "ref-bo", name: "Bo" }] },
    });

    const buyer = await screen.findByRole("group", { name: "Buyer" });
    fireEvent.click(within(buyer).getByRole("combobox"));

    await screen.findByRole("option", { name: /Bo/ });
  });
});
