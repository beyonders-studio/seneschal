import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductVariationRead, RecipeRead } from "@/api/generated/hoard.schemas";
import { productDetail, SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

const variation = (fields: Partial<ProductVariationRead> = {}): ProductVariationRead => ({
  id: 20,
  attributes: {},
  sku: null,
  is_default: true,
  price: null,
  average_cost: null,
  on_hand: "0",
  stock: [],
  lots: [],
  ...fields,
});

const BOX: RecipeRead = {
  id: 4,
  output: "bundle",
  output_id: 7,
  product_id: 7,
  name: "Chai",
  attributes: {},
  unit_id: null,
  batch_quantity: "1.000000",
  batches: "1",
  minutes: null,
  lines: [
    {
      item: { id: 30, kind: "variation", name: "Tin of oolong", attributes: {} },
      unit_id: null,
      quantity: "3.000000",
      percent: null,
    },
  ],
};

const LARGE: RecipeRead = {
  ...BOX,
  id: 5,
  output: "variation",
  output_id: 21,
  attributes: { size: "Large" },
  unit_id: null,
  lines: [
    {
      item: { id: 5, kind: "material", name: "Black tea", attributes: {} },
      unit_id: 1,
      quantity: "12.000000",
      percent: "100.00",
    },
  ],
};

const opened = (product: ReturnType<typeof productDetail>, ...items: RecipeRead[]) => ({
  ...SETTINGS,
  "GET /api/v1/products/7": { status: 200, data: product },
  "GET /api/v1/products/7/images": { status: 200, data: [] },
  "GET /api/v1/recipes": { status: 200, data: { items, total: items.length } },
});

describe("RecipeSection", () => {
  it("offers a recipe for a product with none, or to make it a bundle", async () => {
    await openSignedIn(
      "/stock/products/7",
      signedIn(),
      opened(productDetail({ variations: [variation()] }))
    );

    await screen.findByText("No recipe yet.");
    screen.getByRole("button", { name: "Add recipe" });
    fireEvent.click(screen.getByRole("button", { name: "Make it a bundle" }));

    const dialog = await screen.findByRole("dialog", { name: "What Chai holds" });
    within(dialog).getByText("What it holds");
    expect(within(dialog).queryByLabelText("One batch makes")).toBeNull();
  });

  it("shows each variation's recipe under its name", async () => {
    await openSignedIn(
      "/stock/products/7",
      signedIn(),
      opened(
        productDetail({
          variations: [
            variation(),
            variation({ id: 21, attributes: { size: "Large" }, is_default: false }),
          ],
        }),
        LARGE
      )
    );

    await screen.findByRole("heading", { name: "Recipe for Large" });
    screen.getByText("No recipe for Default yet.");
    await screen.findByText("12 g");
    expect(screen.queryByRole("button", { name: "Make it a bundle" })).toBeNull();
  });

  it("shows what a bundle holds, with no batch or shares", async () => {
    await openSignedIn(
      "/stock/products/7",
      signedIn(),
      opened(productDetail({ variations: [variation()] }), BOX)
    );

    await screen.findByRole("heading", { name: "Holds" });
    screen.getByText("Tin of oolong");
    expect(screen.queryByText("By weight")).toBeNull();
    expect(screen.queryByLabelText("Scale to make")).toBeNull();
  });
});

describe("RecipesPage", () => {
  it("lists recipes by what they make, each linking to its page", async () => {
    await openSignedIn("/production/recipes", signedIn(), {
      ...SETTINGS,
      "GET /api/v1/recipes": { status: 200, data: { items: [BOX, LARGE], total: 2 } },
    });

    const box = (await screen.findByRole("link", { name: "Chai" })).closest("tr");
    if (!box) throw new Error("no row");
    within(box).getByText("Bundle");
    expect(screen.getByRole("link", { name: /Chai.*Large/ }).getAttribute("href")).toContain(
      "/stock/products/7"
    );
  });
});
