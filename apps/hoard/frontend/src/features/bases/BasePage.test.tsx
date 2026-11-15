import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RecipeRead } from "@/api/generated/hoard.schemas";
import { base, baseDetail, SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

const SPICE = baseDetail({
  on_hand: "400.000000",
  stock: [{ location_id: 10, quantity: "400.000000" }],
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
  minutes: 20,
  lines: [
    {
      item: { id: 5, kind: "material", name: "Black tea", attributes: {} },
      unit_id: 1,
      written: "300.000000",
      amount: "300.000000",
      ratio: "1.5",
      tracking_unit_id: 1,
      quantity: "300.000000",
      percent: "60.00",
    },
    {
      item: { id: 9, kind: "base", name: "Syrup", attributes: {} },
      unit_id: 1,
      written: "200.000000",
      amount: "200.000000",
      ratio: "1",
      tracking_unit_id: 1,
      quantity: "200.000000",
      percent: "40.00",
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
