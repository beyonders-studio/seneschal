import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { baseDetail, materialDetail, productDetail, SETTINGS } from "@/features/items/testing";
import { lotRead } from "@/features/lots/testing";
import { orderRead } from "@/features/orders/testing";
import { openSignedIn, signedIn } from "@/testing";

const NONE = { status: 200, data: { items: [], total: 0 } };
const STAMPS = {
  created_at: "2026-11-01T09:00:00Z",
  created_by: null,
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: null,
};

/** What Stock's pages read: no records of any kind, but the ones opened. */
const STOCK = {
  ...SETTINGS,
  "GET /api/v1/materials": NONE,
  "GET /api/v1/bases": NONE,
  "GET /api/v1/recipes": NONE,
  "GET /api/v1/products": NONE,
  "GET /api/v1/purchases": NONE,
  "GET /api/v1/adjustments": NONE,
  "GET /api/v1/stocktakes": NONE,
  "GET /api/v1/materials/3": { status: 200, data: materialDetail({ id: 3 }) },
  "GET /api/v1/bases/3": { status: 200, data: baseDetail({ id: 3 }) },
  "GET /api/v1/products/3": { status: 200, data: productDetail({ id: 3 }) },
  "GET /api/v1/products/3/images": { status: 200, data: [] },
  "GET /api/v1/lots/3": { status: 200, data: lotRead() },
  "GET /api/v1/purchases/3": {
    status: 200,
    data: {
      id: 3,
      code: "P-0003",
      dated_on: "2026-11-01",
      supplier_contact_id: null,
      location_id: 1,
      paid: false,
      discount: "0.000000",
      shipping: "0.000000",
      tax: "0.000000",
      total: "0.000000",
      lines: [],
      ...STAMPS,
    },
  },
  "GET /api/v1/stocktakes/3": {
    status: 200,
    data: {
      id: 3,
      kind: "material",
      location_id: 1,
      category_id: null,
      sample_size: null,
      dated_on: "2026-11-05",
      notes: null,
      row_count: 0,
      confirmed_count: 0,
      accuracy: null,
      finished: false,
      rows: [],
      ...STAMPS,
    },
  },
};

/** What Production's pages read: no runs, but the one opened. */
const PRODUCTION = {
  ...SETTINGS,
  "GET /api/v1/runs": NONE,
  "GET /api/v1/runs/3": {
    status: 200,
    data: {
      id: 3,
      code: "R-0003",
      item: { id: 8, kind: "base", name: "Dough", attributes: {} },
      status: "not_started",
      location_id: 1,
      initiative_id: 7,
      planned_batches: "1.000000",
      planned_quantity: "1.000000",
      actual_quantity: null,
      assignee_ref: null,
      started_on: null,
      deadline_on: null,
      completed_on: null,
      uncosted: false,
      recipe_id: 2,
      labour_minutes: null,
      expires_on: null,
      output_lot: null,
      lines: [],
      ...STAMPS,
    },
  },
};

/** Waits for a page with tabs, and answers which tab is selected. */
const selectedTab = async (page: string) => {
  await screen.findByRole("heading", { level: 1, name: page });
  return screen.getByRole("tab", { selected: true }).textContent;
};

const stockTab = () => selectedTab("Stock");

describe("Hoard's pages", () => {
  it.each([
    ["/stock/materials", "Materials", "No materials yet"],
    ["/stock/bases", "Bases", "No bases yet"],
    ["/stock/products", "Products", "No products yet"],
    ["/stock/purchases", "Purchases", "No purchases yet"],
    ["/stock/adjustments", "Adjustments", "No adjustments yet"],
    ["/stock/stocktakes", "Stocktakes", "No stocktakes yet"],
    ["/stock/recall", "Recall", "Recall a lot"],
  ])("%s opens under Stock, on its tab", async (path, tab, empty) => {
    await openSignedIn(path, signedIn(), STOCK);
    expect(await stockTab()).toBe(tab);
    await screen.findByText(empty);
  });

  it.each([
    ["/stock/materials/3", "Materials", "Oolong"],
    ["/stock/bases/3", "Bases", "Chai spice"],
    ["/stock/products/3", "Products", "Chai"],
    ["/stock/purchases/3", "Purchases", /^Purchase P-0003/],
    ["/stock/stocktakes/3", "Stocktakes", /^Stocktake of/],
  ])("%s opens one record, under its tab", async (path, tab, heading) => {
    await openSignedIn(path, signedIn(), STOCK);
    expect(await stockTab()).toBe(tab);
    await screen.findByRole("heading", { level: 2, name: heading });
  });

  it("/stock/lots/3 opens one lot, with the way back to its item", async () => {
    await openSignedIn("/stock/lots/3", signedIn(), STOCK);
    await screen.findByRole("heading", { level: 2, name: "Lot L-3" });
    expect(screen.getByRole("link", { name: "Oolong" }).getAttribute("href")).toBe(
      "/stock/materials/5"
    );
  });

  it("Stock opens on its materials, and its tabs move between records", async () => {
    await openSignedIn("/stock", signedIn(), STOCK);
    expect(await stockTab()).toBe("Materials");
    await screen.findByText("No materials yet");

    fireEvent.mouseDown(screen.getByRole("tab", { name: "Purchases" }));
    await screen.findByText("No purchases yet");
    expect(await stockTab()).toBe("Purchases");
  });

  it.each([
    ["/production", "Runs", "No production runs yet"],
    ["/production/runs", "Runs", "No production runs yet"],
    ["/production/recipes", "Recipes", "No recipes yet"],
  ])("%s opens under Production, on its tab", async (path, tab, empty) => {
    await openSignedIn(path, signedIn(), { ...PRODUCTION, "GET /api/v1/recipes": NONE });
    expect(await selectedTab("Production")).toBe(tab);
    await screen.findByText(empty);
  });

  it("/production/runs/3 opens one run, under its tab", async () => {
    await openSignedIn("/production/runs/3", signedIn(), PRODUCTION);
    expect(await selectedTab("Production")).toBe("Runs");
    await screen.findByRole("heading", { level: 2, name: "Run R-0003" });
  });

  it.each([
    ["/orders", "Orders", "No orders yet"],
    ["/orders/listings", "Listings", "No listings yet"],
  ])("%s opens under Orders, on its tab", async (path, tab, empty) => {
    await openSignedIn(path, signedIn(), {
      "GET /api/v1/orders": NONE,
      "GET /api/v1/listings": NONE,
    });
    expect(await selectedTab("Orders")).toBe(tab);
    await screen.findByText(empty);
  });

  it("/orders/3 opens one order, under its tab", async () => {
    await openSignedIn("/orders/3", signedIn(), {
      "GET /api/v1/orders/3": { status: 200, data: orderRead({ id: 3, code: "O-0003" }) },
    });
    expect(await selectedTab("Orders")).toBe("Orders");
    await screen.findByRole("heading", { level: 2, name: /Order O-0003/ });
  });

  it("/reports opens its page", async () => {
    await openSignedIn("/reports");
    await screen.findByRole("heading", { level: 1, name: "Reports" });
    await screen.findByText("Stock to reorder");
  });

  it("/settings opens with a section for each setting", async () => {
    await openSignedIn("/settings");
    await screen.findByRole("heading", { level: 1, name: "Settings" });
    for (const section of [
      "Currency",
      "Units",
      "Categories",
      "Locations",
      "Adjustment reasons",
      "Hold reasons",
      "Tasks",
      "Stores",
      "Auto-manufacture",
      "Backups",
    ]) {
      await screen.findByRole("heading", { level: 2, name: section });
    }
  });
});
