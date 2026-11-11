import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { product, SETTINGS } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const list = (...items: ReturnType<typeof product>[]) => ({
  "GET /api/v1/products": { status: 200, data: { items, total: items.length } },
});

describe("ProductsPage", () => {
  it("lists products with their stock and status, and no prices", async () => {
    await openSignedIn("/stock/products", signedIn(), {
      ...SETTINGS,
      ...list(
        product({ on_hand: "1", low_stock_limit: "2.000000" }),
        product({ id: 8, name: "Matcha latte", status: "archived", on_hand: "12" })
      ),
    });

    const chai = (await screen.findByRole("link", { name: "Chai" })).closest("tr");
    const latte = screen.getByRole("link", { name: "Matcha latte" }).closest("tr");
    if (!chai || !latte) throw new Error("no rows");
    await within(chai).findByText("Drinks");
    within(chai).getByText("Low stock");
    within(latte).getByText("Archived");
    expect(screen.queryByText("Default price")).toBeNull();
  });

  it("shows default prices to a member who sees costs", async () => {
    await openSignedIn("/stock/products", signedIn({ permissions: ["use", SEE_COSTS] }), {
      ...SETTINGS,
      ...list(product({ default_price: "12.000000" })),
    });

    await screen.findByText("Default price");
    await screen.findByText("$12.00");
  });
});
