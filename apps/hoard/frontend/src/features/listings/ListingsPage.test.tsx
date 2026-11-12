import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ListingRead } from "@/api/generated/hoard.schemas";
import { openSignedIn, signedIn } from "@/testing";

const listing = (fields: Partial<ListingRead>): ListingRead => ({
  id: 1,
  store_id: 3,
  channel: "shopify",
  product_id: "p1",
  variant_id: "v1",
  inventory_item_id: "i1",
  sku: null,
  title: "Chai tin",
  item_id: null,
  item_name: null,
  item_kind: null,
  item_product_id: null,
  matched_by: null,
  ...fields,
});

const LISTINGS = [
  listing({
    sku: "CHAI",
    item_id: 20,
    item_name: "Chai Large",
    item_kind: "variation",
    item_product_id: 7,
    matched_by: "sku",
  }),
  listing({ id: 2, product_id: "p2", title: "Mug" }),
];

describe("ListingsPage", () => {
  it("lists each listing with the item it sells and how it was linked", async () => {
    await openSignedIn("/orders/listings", signedIn({ page: "orders" }), {
      "GET /api/v1/listings": { status: 200, data: { items: LISTINGS, total: 2 } },
    });

    const chai = (await screen.findByText("Chai tin")).closest("tr") as HTMLElement;
    expect(within(chai).getByRole("link", { name: "Chai Large" }).getAttribute("href")).toBe(
      "/stock/products/7"
    );
    expect(within(chai).getByText("SKU")).toBeTruthy();
    const mug = screen.getByText("Mug").closest("tr") as HTMLElement;
    expect(within(mug).getByText("Nothing yet")).toBeTruthy();
  });

  it("says so while no store has listed anything", async () => {
    await openSignedIn("/orders/listings", signedIn({ page: "orders" }), {
      "GET /api/v1/listings": { status: 200, data: { items: [], total: 0 } },
    });

    expect(await screen.findByText("No listings yet")).toBeTruthy();
  });

  it("unlinks a listing by hand", async () => {
    const asked = await openSignedIn("/orders/listings", signedIn({ page: "orders" }), {
      "GET /api/v1/listings": { status: 200, data: { items: LISTINGS, total: 2 } },
      "PUT /api/v1/listings/1": { status: 200, data: { ...LISTINGS[0], item_id: null } },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Link Chai tin" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Sell nothing" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(asked.find((request) => request.method === "put")?.data).toBe(
        JSON.stringify({ item_id: null })
      )
    );
  });
});
