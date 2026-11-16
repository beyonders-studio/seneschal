import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HoardPropertySummary } from "@/api/generated/hoard.schemas";
import { materialDetail, SETTINGS, stockRow } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

// The type picker scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const OOLONG = materialDetail({
  on_hand: "13.000000",
  low_stock_limit: "5.000000",
  purchase_unit_id: 1,
  purchase_conversion: "250.000000",
  average_cost: "2.000000",
  stock: [stockRow(10, "10.000000"), stockRow(11, "3.000000")],
  lots: [
    {
      id: 1,
      location_id: 10,
      number: "L-42",
      dated_on: "2026-11-01",
      expires_on: null,
      notes: null,
      balance: "10.000000",
      on_hold: false,
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

  it("shows what is on order, its usage and the recipes it goes into", async () => {
    const usage = (received: string, used: string) => ({ received, used });
    await openSignedIn("/stock/materials/5", signedIn(), {
      ...SETTINGS,
      "GET /api/v1/materials/5": {
        status: 200,
        data: {
          ...OOLONG,
          on_order: "253.592000",
          overdue: true,
          usage: OOLONG.usage.map((row) => ({
            ...row,
            ...(row.window === "week" ? usage("100", "20") : usage("180", "30")),
          })),
          used_in: [
            {
              recipe_id: 3,
              output: "variation",
              output_id: 20,
              product_id: 7,
              name: "Oolong tin",
              attributes: {},
              archived: false,
              batch_quantity: "1.000000",
              batch_unit_id: null,
              amount: "50.000000",
              unit_id: 1,
            },
            {
              recipe_id: 4,
              output: "base",
              output_id: 8,
              product_id: null,
              name: "Sleepy blend",
              attributes: {},
              archived: true,
              batch_quantity: "300.000000",
              batch_unit_id: 1,
              amount: "100.000000",
              unit_id: 1,
            },
          ],
        },
      },
    });

    const onOrder = (await screen.findByText("253.592 g")).closest("span");
    if (!onOrder) throw new Error("no on order");
    within(onOrder).getByText("Overdue");
    const week = screen.getByText("The last week").closest("tr");
    if (!week) throw new Error("no week");
    within(week).getByText("100 g");
    within(week).getByText("20 g");
    const all = screen.getByText("All time").closest("tr");
    if (!all) throw new Error("no all time");
    within(all).getByText("180 g");
    expect(screen.getByRole("link", { name: "Oolong tin" }).getAttribute("href")).toContain(
      "/stock/products/7"
    );
    const blend = screen.getByRole("link", { name: "Sleepy blend" }).closest("tr");
    if (!blend) throw new Error("no blend");
    within(blend).getByText("Archived");
    within(blend).getByText("100 g");
    within(blend).getByText("300 g");
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

  describe("by its type", () => {
    /** Tins: packaging, whose page asks for each tin's origin. */
    const typed = {
      ...opened,
      "GET /api/v1/settings/types": {
        status: 200,
        data: [
          { id: 30, kind: "material", name: null, shape: "ingredient", is_default: true },
          { id: 33, kind: "material", name: "Tins", shape: "packaging", is_default: false },
        ],
      },
      "GET /api/v1/declarations/material/5": {
        status: 200,
        data: {
          records_own: true,
          recorded_allergens: ["milk"],
          ingredient_name: null,
          ingredients: ["Oolong"],
          unweighed: [],
          allergens: ["milk"],
          undeclared: [],
          recorded_fragrance: null,
          fragrance_recorded: false,
          rinse_off: false,
          fragrance_threshold: "0.001",
          fragrance_shares: {},
          fragrance_allergens: [],
          fragrance_undeclared: ["Oolong"],
        },
      },
      "GET /api/v1/properties": {
        status: 200,
        data: [{ id: 9, kind: "material", name: "Origin", type: "text", options: null }],
      },
      "GET /api/v1/layouts/material": {
        status: 200,
        data: {
          layouts: [
            {
              kind: "table",
              is_default: true,
              hidden: false,
              definition: {},
              updated_at: null,
            },
            { kind: "material", type_id: 30, definition: {}, updated_at: null },
            {
              kind: "material",
              type_id: 33,
              definition: {
                main: [{ type: "field", props: { field: "property:9", required: true } }],
              },
              updated_at: "2026-11-15T10:00:00Z",
            },
          ],
        },
      },
    };

    it("is drawn as its type's shape, which shows or hides what it records", async () => {
      await openSignedIn("/stock/materials/5", signedIn(), typed);

      // An ingredient shows the allergens it records, and declares them.
      await screen.findByText("Allergens and ingredients");
      expect(await screen.findAllByText("Milk")).toHaveLength(2);
      screen.getByText("Ingredient name");
      // Neither its ingredient name nor its fragrance allergens are recorded.
      expect(screen.getAllByText("Not recorded")).toHaveLength(2);
    });

    it("hides what packaging has no use for, which it still records", async () => {
      await openSignedIn("/stock/materials/5", signedIn(), {
        ...typed,
        "GET /api/v1/materials/5": {
          status: 200,
          data: { ...OOLONG, type_id: 33 },
        },
        "GET /api/v1/layouts/material": { status: 200, data: { layouts: [] } },
      });

      await screen.findByRole("heading", { name: "Oolong" });
      await screen.findByText("Tins");
      expect(screen.queryByText("Allergens and ingredients")).toBeNull();
      expect(screen.queryByText("Ingredient name")).toBeNull();
    });

    it("asks for what its new type's page requires as it moves", async () => {
      const asked = await openSignedIn("/stock/materials/5", signedIn(), {
        ...typed,
        "PUT /api/v1/materials/5": { status: 200, data: { ...OOLONG, type_id: 33 } },
        "PUT /api/v1/properties/values/material/5": {
          status: 200,
          data: [{ ...ORIGIN, value: "Anxi" }],
        },
      });
      fireEvent.click(await screen.findByRole("button", { name: "Edit" }));
      const dialog = await screen.findByRole("dialog", { name: "Edit material" });
      fireEvent.click(within(dialog).getByRole("combobox", { name: "Type" }));
      fireEvent.click(await screen.findByRole("option", { name: "Tins" }));
      fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

      const asking = await screen.findByRole("dialog", { name: "What the Tins page asks for" });
      expect(asked.some((request) => request.method === "put")).toBe(false);
      const origin = await within(asking).findByRole("textbox");
      fireEvent.change(origin, { target: { value: "Anxi" } });
      fireEvent.click(within(asking).getByRole("button", { name: "Continue" }));

      await waitFor(() =>
        expect(
          asked.find((request) => request.url === "/api/v1/properties/values/material/5")?.data
        ).toBe(JSON.stringify({ values: [{ property_id: 9, value: "Anxi" }] }))
      );
      const moved = asked.find(
        (request) => request.method === "put" && request.url === "/api/v1/materials/5"
      );
      expect(JSON.parse(String(moved?.data)).type_id).toBe(33);
    });
  });
});
