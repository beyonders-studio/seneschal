import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { materialDetail, productDetail, SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

/** What the catalog's pages read: no materials or products, but the ones opened. */
const CATALOG = {
  ...SETTINGS,
  "GET /api/v1/materials": { status: 200, data: { items: [], total: 0 } },
  "GET /api/v1/products": { status: 200, data: { items: [], total: 0 } },
  "GET /api/v1/materials/3": { status: 200, data: materialDetail({ id: 3 }) },
  "GET /api/v1/products/3": { status: 200, data: productDetail({ id: 3 }) },
  "GET /api/v1/products/3/images": { status: 200, data: [] },
};

/** Waits for Stock's page, and answers which tab is selected. */
const stockTab = async () => {
  await screen.findByRole("heading", { level: 1, name: "Stock" });
  return screen.getByRole("tab", { selected: true }).textContent;
};

describe("Hoard's pages", () => {
  it.each([
    ["/stock/materials", "Materials", "No materials yet"],
    ["/stock/products", "Products", "No products yet"],
    ["/stock/purchases", "Purchases", "No purchases yet"],
    ["/stock/adjustments", "Adjustments", "No adjustments yet"],
    ["/stock/stocktakes", "Stocktakes", "No stocktakes yet"],
  ])("%s opens under Stock, on its tab", async (path, tab, empty) => {
    await openSignedIn(path, signedIn(), CATALOG);
    expect(await stockTab()).toBe(tab);
    await screen.findByText(empty);
  });

  it.each([
    ["/stock/materials/3", "Materials", "Oolong"],
    ["/stock/products/3", "Products", "Chai"],
    ["/stock/purchases/3", "Purchases", "Purchase"],
    ["/stock/stocktakes/3", "Stocktakes", "Stocktake"],
  ])("%s opens one record, under its tab", async (path, tab, heading) => {
    await openSignedIn(path, signedIn(), CATALOG);
    expect(await stockTab()).toBe(tab);
    await screen.findByRole("heading", { level: 2, name: heading });
  });

  it("Stock opens on its materials, and its tabs move between records", async () => {
    await openSignedIn("/stock", signedIn(), CATALOG);
    expect(await stockTab()).toBe("Materials");
    await screen.findByText("No materials yet");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Purchases" }));
    await screen.findByText("No purchases yet");
    expect(await stockTab()).toBe("Purchases");
  });

  it.each([
    ["/production", "Production", "No production runs yet"],
    ["/reports", "Reports", "Stock to reorder"],
  ])("%s opens its page", async (path, title, empty) => {
    await openSignedIn(path);
    await screen.findByRole("heading", { level: 1, name: title });
    await screen.findByText(empty);
  });

  it("/settings opens with a section for each setting", async () => {
    await openSignedIn("/settings");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    for (const section of ["Currency", "Units", "Categories", "Locations", "Adjustment reasons"]) {
      await screen.findByRole("heading", { level: 2, name: section });
    }
  });
});
