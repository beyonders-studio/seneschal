import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RecipeRead } from "@/api/generated/hoard.schemas";
import { base, baseDetail, product, SETTINGS, stockRow } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const SPICE = baseDetail({
  on_hand: "400.000000",
  stock: [stockRow(10, "400.000000")],
});

const RECIPE: RecipeRead = {
  id: 3,
  output: "base",
  output_id: 8,
  product_id: null,
  name: "Chai spice",
  attributes: {},
  unit_id: 1,
  by_ratio: false,
  total: null,
  total_unit_id: null,
  batch_quantity: "500.000000",
  batches: "1",
  cost: null,
  minutes: 20,
  notes: "Toast the spice first.",
  lines: [
    {
      item: { id: 5, kind: "material", name: "Black tea", attributes: {}, archived: false },
      unit_id: 1,
      written: "300.000000",
      amount: "300.000000",
      ratio: "1.5",
      tracking_unit_id: 1,
      quantity: "300.000000",
      percent: "60.00",
      on_hand: "0",
      cost: null,
    },
    {
      item: { id: 9, kind: "base", name: "Syrup", attributes: {}, archived: false },
      unit_id: 1,
      written: "200.000000",
      amount: "200.000000",
      ratio: "1",
      tracking_unit_id: 1,
      quantity: "200.000000",
      percent: "40.00",
      on_hand: "0",
      cost: null,
    },
  ],
};

const recipes = (...items: RecipeRead[]) => ({
  "GET /api/v1/recipes": { status: 200, data: { items, total: items.length } },
});

const opened = { ...SETTINGS, "GET /api/v1/bases/8": { status: 200, data: SPICE } };

describe("BasePage", () => {
  it("shows a base's recipe, each line's share by weight, and its stock", async () => {
    await openSignedIn("/stock/bases/8", signedIn(), { ...opened, ...recipes(RECIPE) });

    await screen.findByRole("heading", { name: "Chai spice" });
    await screen.findByText("One batch makes 500 g · 20 minutes");
    const [lines, stock] = screen.getAllByRole("table");
    within(lines).getByText("Black tea");
    within(lines).getByText("300 g");
    within(lines).getByText("60%");
    within(lines).getByText("40%");
    await within(stock).findByText("400 g");
  });

  it("shows its picture, notes and bins and its recipe's method, and sets a bin", async () => {
    const asked = await openSignedIn("/stock/bases/8", signedIn(), {
      ...SETTINGS,
      ...recipes({
        ...RECIPE,
        lines: [{ ...RECIPE.lines[0], item: { ...RECIPE.lines[0].item, archived: true } }],
      }),
      "GET /api/v1/bases/8": {
        status: 200,
        data: {
          ...SPICE,
          notes: "Keeps a month.",
          picture_id: 4,
          stock: [stockRow(10, "400.000000", { bin: { zone: "A", section: "2", position: null } })],
        },
      },
      "PUT /api/v1/items/8/bins/10": { status: 204 },
    });

    const picture = await screen.findByRole("img", { name: "Chai spice" });
    expect(picture.getAttribute("src")).toBe("/api/v1/items/8/picture/4");
    screen.getByText("Keeps a month.");
    await screen.findByText("Toast the spice first.");
    const tea = (await screen.findByText("Black tea")).closest("tr");
    if (!tea) throw new Error("no line");
    within(tea).getByText("Archived");
    await screen.findByText("A, 2");

    fireEvent.click(screen.getByRole("button", { name: "Bin at Main location" }));
    const dialog = await screen.findByRole("dialog", { name: "Bin at Main location" });
    fireEvent.change(within(dialog).getByLabelText("Position"), { target: { value: "Top" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(asked.find((request) => request.url === "/api/v1/items/8/bins/10")?.data).toBe(
        JSON.stringify({ zone: "A", section: "2", position: "Top" })
      )
    );
  });

  it("scales the recipe to make another quantity", async () => {
    const asked = await openSignedIn("/stock/bases/8", signedIn(), {
      ...opened,
      ...recipes(RECIPE),
      "GET /api/v1/recipes/3": {
        status: 200,
        data: {
          ...RECIPE,
          batch_quantity: "50.000000",
          batches: "0.1",
          lines: RECIPE.lines.map((line) => ({
            ...line,
            quantity: String(Number(line.quantity) / 10),
            amount: String(Number(line.amount) / 10),
          })),
        },
      },
    });

    fireEvent.change(await screen.findByLabelText("Scale to"), { target: { value: "50" } });

    await screen.findByText("30 g");
    screen.getByText("One batch makes 50 g · 20 minutes");
    await waitFor(() =>
      expect(asked.find((request) => request.url === "/api/v1/recipes/3")?.params).toEqual({
        quantity: "50",
      })
    );
  });

  it("shows its figures, could make and last stocktake per location, and its history", async () => {
    const asked = await openSignedIn("/stock/bases/8", signedIn(), {
      ...SETTINGS,
      ...recipes(RECIPE),
      "GET /api/v1/bases/8": {
        status: 200,
        data: baseDetail({
          on_hand: "400.000000",
          potential: "1050.000000",
          made: "590.000000",
          last_stocktake: "2026-11-03",
          stock: [
            stockRow(10, "400.000000", { could_make: "450.000000", last_stocktake: "2026-11-01" }),
            stockRow(11, "0", { could_make: "600.000000", last_stocktake: "2026-11-03" }),
          ],
        }),
      },
      "GET /api/v1/runs": {
        status: 200,
        data: {
          items: [
            {
              id: 4,
              code: "R-0004",
              item: { id: 8, kind: "base", name: "Chai spice", attributes: {}, archived: false },
              status: "completed",
              location_id: 10,
              initiative_id: null,
              planned_batches: "1",
              planned_quantity: "300.000000",
              actual_quantity: "290.000000",
              assignee_ref: null,
              started_on: "2026-11-04",
              deadline_on: null,
              completed_on: "2026-11-04",
              uncosted: false,
            },
          ],
          total: 1,
        },
      },
      "GET /api/v1/stocktakes": {
        status: 200,
        data: {
          items: [
            {
              id: 6,
              kind: "base",
              location_id: 11,
              category_id: null,
              sample_size: null,
              dated_on: "2026-11-03",
              notes: null,
              row_count: 1,
              confirmed_count: 1,
              accuracy: "0",
              finished: true,
              created_at: "2026-11-03T10:00:00Z",
              created_by: null,
              count: { expected: "10.000000", counted: "0.000000" },
            },
          ],
          total: 1,
        },
      },
      "GET /api/v1/adjustments": { status: 200, data: { items: [], total: 0 } },
    });

    await screen.findByText("1,050 g");
    screen.getByText("590 g");
    screen.getByText("The last 12 months");
    screen.getByText("No recipe uses it yet.");
    const back = (await screen.findByText("600 g")).closest("tr");
    if (!back) throw new Error("no row");
    within(back).getByText("Back room");
    within(back).getByText("Nov 3, 2026");
    expect(await screen.findAllByRole("link", { name: "R-0004" })).toHaveLength(2);
    await screen.findByText("Nothing has moved its stock yet.");
    const counted = (await screen.findByRole("link", { name: "Nov 3, 2026" })).closest("tr");
    if (!counted) throw new Error("no stocktake");
    within(counted).getByText("10");
    await waitFor(() =>
      expect(
        asked
          .filter((request) => ["/api/v1/runs", "/api/v1/stocktakes"].includes(request.url ?? ""))
          .map((request) => request.params)
      ).toEqual(
        expect.arrayContaining([
          { offset: 0, limit: 10, item_id: 8 },
          { offset: 0, limit: 10, used_item_id: 8 },
        ])
      )
    );
  });

  it("costs its recipe only for a member who sees costs", async () => {
    const costed: RecipeRead = {
      ...RECIPE,
      lines: [
        {
          ...RECIPE.lines[0],
          on_hand: "1000.000000",
          cost: { amount: "12.000000", last_known: false },
        },
        { ...RECIPE.lines[1], on_hand: "0", cost: { amount: "3.000000", last_known: true } },
        {
          ...RECIPE.lines[1],
          item: { ...RECIPE.lines[1].item, id: 12, name: "Lychee flavor" },
          on_hand: "40.000000",
          cost: { amount: null, last_known: false },
        },
      ],
      cost: { labour: "10.000000", total: "25.000000", incomplete: true, unit_cost: "0.05" },
    };
    await openSignedIn("/stock/bases/8", signedIn({ permissions: ["use", SEE_COSTS] }), {
      ...opened,
      ...recipes(costed),
    });

    const card = (await screen.findByText("Black tea")).closest("section");
    if (!card) throw new Error("no card");
    await within(card).findByText("$12.00");
    const syrup = within(card).getByText("Syrup").closest("tr");
    if (!syrup) throw new Error("no line");
    within(syrup).getByText("Last known");
    within(syrup).getByText("$3.00");
    const lychee = within(card).getByText("Lychee flavor").closest("tr");
    if (!lychee) throw new Error("no line");
    within(lychee).getByText("No cost yet");
    within(lychee).getByText("40 g");
    within(card).getByText("Labour, 20 minutes a batch");
    within(card).getByText("$10.00");
    within(card).getByText("A cost is missing");
    within(card).getByText("Batch cost $25.00");
    within(card).getByText("Manufacture cost $0.05 a g");
  });

  it("shows on hand but no cost to a member who doesn't see costs", async () => {
    await openSignedIn("/stock/bases/8", signedIn(), {
      ...opened,
      ...recipes({
        ...RECIPE,
        lines: RECIPE.lines.map((line) => ({ ...line, on_hand: "70.000000" })),
      }),
    });

    const card = (await screen.findByText("Black tea")).closest("section");
    if (!card) throw new Error("no card");
    expect(within(card).getAllByText("70 g")).toHaveLength(2);
    expect(within(card).queryByRole("columnheader", { name: "Cost" })).toBeNull();
    expect(within(card).queryByText(/Batch cost/)).toBeNull();
  });

  it("offers to add a recipe until it has one", async () => {
    await openSignedIn("/stock/bases/8", signedIn(), { ...opened, ...recipes() });

    await screen.findByText("No recipe yet.");
    fireEvent.click(screen.getByRole("button", { name: "Add recipe" }));
    await screen.findByRole("dialog", { name: "Recipe for Chai spice" });
    screen.getByLabelText("One batch makes");
  });

  it("leaves out changing it while Hoard is read-only", async () => {
    await openSignedIn("/stock/bases/8", signedIn({ read_only: true }), {
      ...opened,
      ...recipes(RECIPE),
    });

    await screen.findByText("Black tea");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
    expect(screen.queryByLabelText("Scale to")).not.toBeNull();
  });
});

describe("RecipeDialog", () => {
  it("switches a recipe to a ratio as Hoard works it out, and back as it was", async () => {
    const preview = (byRatio: boolean) => ({
      ...RECIPE,
      by_ratio: byRatio,
      total: byRatio ? "500.000000" : null,
      total_unit_id: byRatio ? 1 : null,
      lines: RECIPE.lines.map((line, index) => ({
        ...line,
        written: byRatio ? ["3.000000", "2.000000"][index] : line.written,
        ratio: ["1.5", "1"][index],
        // The syrup is counted in a unit Hoard can't weigh.
        percent: ["100.00", null][index],
      })),
      switched: {
        batch_quantity: "500.000000",
        minutes: 20,
        by_ratio: !byRatio,
        total: byRatio ? null : "500.000000",
        total_unit_id: byRatio ? null : 1,
        lines: RECIPE.lines.map((line, index) => ({
          item_id: line.item.id,
          written: byRatio ? line.amount : ["3.000000", "2.000000"][index],
          unit_id: 1,
        })),
      },
    });
    const asked = await openSignedIn("/stock/bases/8", signedIn(), {
      ...opened,
      ...recipes(RECIPE),
      "POST /api/v1/recipes/preview": { status: 200, data: preview(false) },
    });
    const card = (await screen.findByText("Black tea")).closest("section");
    if (!card) throw new Error("no card");
    fireEvent.click(within(card).getByRole("button", { name: "Edit" }));
    const dialog = await screen.findByRole("dialog", { name: "Recipe for Chai spice" });
    await within(dialog).findByText("1.5");
    const unweighed = within(dialog).getByTitle("Not measured by weight, so it has no share.");
    expect(unweighed.textContent).toBe("–");

    fireEvent.click(within(dialog).getByRole("switch", { name: "Written by ratio" }));

    const [tea, syrup] = within(dialog).getAllByLabelText("Parts");
    expect([tea, syrup].map((field) => (field as HTMLInputElement).value)).toEqual([
      "3.000000",
      "2.000000",
    ]);
    expect(within(dialog).getByLabelText("Ingredients total")).toHaveProperty(
      "value",
      "500.000000"
    );
    fireEvent.click(within(dialog).getByRole("switch", { name: "Written by ratio" }));
    expect(
      within(dialog)
        .getAllByLabelText("Quantity")
        .map((field) => (field as HTMLInputElement).value)
    ).toEqual(["300.000000", "200.000000"]);
    await waitFor(() =>
      expect(asked.some((request) => request.url === "/api/v1/recipes/preview")).toBe(true)
    );
  });

  it("offers products' variations as lines, but never a bundle's", async () => {
    await openSignedIn("/stock/bases/8", signedIn(), {
      ...opened,
      ...recipes(),
      "GET /api/v1/materials": { status: 200, data: { items: [], total: 0 } },
      "GET /api/v1/bases": { status: 200, data: { items: [base()], total: 1 } },
      "GET /api/v1/products": {
        status: 200,
        data: {
          items: [product({ name: "Infuser" }), product({ id: 8, name: "Gift set", bundle: true })],
          total: 2,
        },
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Add recipe" }));
    const dialog = await screen.findByRole("dialog", { name: "Recipe for Chai spice" });
    fireEvent.click(within(dialog).getByRole("combobox", { name: "Item 1" }));

    await screen.findByRole("option", { name: /Infuser/ });
    screen.getByRole("option", { name: /Chai spice/ });
    expect(screen.queryByRole("option", { name: /Gift set/ })).toBeNull();
  });
});

describe("BasesPage", () => {
  it("lists bases with their stock, and asks for the first when there are none", async () => {
    await openSignedIn("/stock/bases", signedIn(), {
      ...SETTINGS,
      "GET /api/v1/bases": {
        status: 200,
        data: { items: [base({ on_hand: "40", low_stock_limit: "100.000000" })], total: 1 },
      },
    });

    const spice = (await screen.findByRole("link", { name: "Chai spice" })).closest("tr");
    if (!spice) throw new Error("no row");
    within(spice).getByText("Low stock");
    await within(spice).findByText("40 g");
    screen.getByRole("button", { name: "Add base" });
  });
});
