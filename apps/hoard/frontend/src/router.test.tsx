import { fireEvent, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { openSignedIn } from "@/testing";

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
    await openSignedIn(path);
    expect(await stockTab()).toBe(tab);
    await screen.findByText(empty);
  });

  it.each([
    ["/stock/materials/3", "Materials", "Material"],
    ["/stock/products/3", "Products", "Product"],
    ["/stock/purchases/3", "Purchases", "Purchase"],
    ["/stock/stocktakes/3", "Stocktakes", "Stocktake"],
  ])("%s opens one record, under its tab", async (path, tab, heading) => {
    await openSignedIn(path);
    expect(await stockTab()).toBe(tab);
    await screen.findByRole("heading", { level: 2, name: heading });
  });

  it("Stock opens on its materials, and its tabs move between records", async () => {
    await openSignedIn("/stock");
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
