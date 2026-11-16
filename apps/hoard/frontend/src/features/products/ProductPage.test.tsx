import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductDetail, ProductVariationRead } from "@/api/generated/hoard.schemas";
import { productDetail, SETTINGS } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const variation = (fields: Partial<ProductVariationRead>): ProductVariationRead => ({
  id: 20,
  attributes: {},
  sku: null,
  is_default: true,
  price: null,
  average_cost: null,
  auto_manufacture: null,
  on_hand: "0",
  stock: [],
  lots: [],
  ...fields,
});

const CHAI = productDetail({
  on_hand: "6.000000",
  default_price: "12.000000",
  variations: [
    variation({
      on_hand: "4.000000",
      stock: [{ location_id: 10, quantity: "4.000000", bin: null }],
    }),
    variation({
      id: 21,
      attributes: { size: "Large" },
      is_default: false,
      price: "15.000000",
      on_hand: "2.000000",
      stock: [{ location_id: 11, quantity: "2.000000", bin: null }],
    }),
  ],
});

/** Chai, listed in a synced Shopify store. */
const LISTED: ProductDetail = {
  ...CHAI,
  store_products: [
    { store_id: 3, product_id: "p1", updated_at: "2026-11-14T09:00:00Z", channel: "shopify" },
  ],
};

const opened = {
  ...SETTINGS,
  "GET /api/v1/products/7": { status: 200, data: CHAI },
  "GET /api/v1/products/7/images": {
    status: 200,
    data: [
      {
        id: 30,
        position: 0,
        content_type: "image/png",
        size: 10,
        created_at: "2026-11-01T10:00:00Z",
        created_by: "member-ref",
      },
    ],
  },
};

describe("ProductPage", () => {
  it("shows a product's pictures and each variation's stock at each location", async () => {
    await openSignedIn("/stock/products/7", signedIn(), opened);

    await screen.findByRole("heading", { name: "Chai" });
    const picture = await screen.findByRole("img", { name: "Chai, picture 1" });
    expect(picture.getAttribute("src")).toBe("/api/v1/products/7/images/30");
    const [variations, stock] = screen.getAllByRole("table");
    within(variations).getByText("Default");
    within(variations).getByText("Large");
    await within(stock).findByText("Main location");
    expect(within(stock).getByText("Back room").closest("tr")?.textContent).toBe("LargeBack room2");
    expect(screen.queryByText("Default price")).toBeNull();
    expect(screen.queryByText("$15.00")).toBeNull();
  });

  it("shows prices to a member who sees costs", async () => {
    await openSignedIn("/stock/products/7", signedIn({ permissions: ["use", SEE_COSTS] }), opened);

    await screen.findByText("Default price");
    await screen.findByText("$12.00");
    screen.getByText("$15.00");
  });

  it("says when the product changed in its store since the page read it", async () => {
    const asked = await openSignedIn("/stock/products/7", signedIn(), {
      ...opened,
      "GET /api/v1/products/7": { status: 200, data: LISTED },
      "PUT /api/v1/products/7": { status: 409, data: { detail: "STORE_PRODUCT_CHANGED" } },
    });

    await screen.findByRole("heading", { name: "Chai" });
    const reads = () => asked.filter((request) => request.url === "/api/v1/products/7").length;
    const before = reads();
    fireEvent.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Edit product" });
    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Masala chai" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await within(dialog).findByText(/This product changed in Shopify since you opened it/);
    const sent = asked.find((request) => request.method === "put");
    expect(JSON.parse(String(sent?.data))).toMatchObject({
      name: "Masala chai",
      store_versions: [{ store_id: 3, product_id: "p1", updated_at: "2026-11-14T09:00:00Z" }],
    });
    // The product is read again, with the store's version.
    await waitFor(() => expect(reads()).toBeGreaterThan(before));
  });

  it("offers to archive a product its store lists instead of deleting it", async () => {
    const asked = await openSignedIn("/stock/products/7", signedIn(), {
      ...opened,
      "GET /api/v1/products/7": { status: 200, data: LISTED },
      "PUT /api/v1/products/7": { status: 200, data: { ...LISTED, status: "archived" } },
    });

    await screen.findByRole("heading", { name: "Chai" });
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Chai can't be deleted here" });
    within(dialog).getByText(/It's listed in Shopify. Archive it instead/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Archive" }));

    await waitFor(() => {
      const sent = asked.find((request) => request.method === "put");
      expect(JSON.parse(String(sent?.data))).toMatchObject({ name: "Chai", status: "archived" });
    });
    expect(asked.some((request) => request.method === "delete")).toBe(false);
  });
});
