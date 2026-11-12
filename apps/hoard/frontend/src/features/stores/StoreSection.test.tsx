import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ListingRead, StoreProductRead, StoreRead } from "@/api/generated/hoard.schemas";
import { productDetail, SETTINGS } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const STORE: StoreRead = {
  id: 3,
  channel: "shopify",
  shop_domain: "tea.myshopify.com",
  account_label: "Tea House",
  connected: true,
  last_check_error: null,
  imported_at: null,
  locations: [],
  listings: 1,
  linked: 1,
  pushes_queued: 0,
  pushes_failed: 0,
};

const LISTING: ListingRead = {
  id: 1,
  store_id: 3,
  channel: "shopify",
  product_id: "p1",
  variant_id: "v1",
  inventory_item_id: "i1",
  sku: "CHAI",
  title: "Chai",
  item_id: 20,
  item_name: "Chai",
  item_kind: "variation",
  item_product_id: 7,
  matched_by: "sku",
};

const HELD: StoreProductRead = {
  store_id: 3,
  id: "p1",
  title: "Chai",
  description: "<p>Spiced</p>",
  status: "active",
  options: [],
  variants: [{ id: "v1", title: "Default Title", sku: "CHAI", price: "12.50", options: {} }],
  images: [{ id: "img-1", url: "https://cdn.test/chai.png", alt: "A tin" }],
  published_on: ["online-store"],
  listings: [LISTING],
};

const opened = (listings: ListingRead[]) => ({
  ...SETTINGS,
  "GET /api/v1/products/7": { status: 200, data: productDetail() },
  "GET /api/v1/products/7/images": { status: 200, data: [] },
  "GET /api/v1/stores": { status: 200, data: [STORE] },
  "GET /api/v1/listings": { status: 200, data: { items: listings, total: listings.length } },
  "GET /api/v1/stores/3/products/p1": { status: 200, data: HELD },
  "PUT /api/v1/stores/3/products/p1": { status: 200, data: HELD },
  "POST /api/v1/stores/3/products": { status: 201, data: HELD },
});

describe("StoreSection", () => {
  it("edits the store's own product live, with its price for a member who sees costs", async () => {
    const asked = await openSignedIn(
      "/stock/products/7",
      signedIn({ permissions: ["use", SEE_COSTS] }),
      opened([LISTING])
    );

    const store = await screen.findByRole("region", { name: "Tea House" });
    const title = await within(store).findByLabelText("Title in the store");
    fireEvent.change(title, { target: { value: "Masala chai" } });
    fireEvent.change(within(store).getByLabelText("Price Default Title"), {
      target: { value: "14" },
    });
    fireEvent.click(within(store).getByRole("button", { name: "Save to the store" }));

    await waitFor(() => {
      const sent = asked.find((request) => request.method === "put");
      expect(JSON.parse(String(sent?.data))).toEqual({
        title: "Masala chai",
        description: "<p>Spiced</p>",
        status: "active",
        variants: [{ id: "v1", sku: "CHAI", price: "14" }],
        images: [{ id: "img-1", url: "https://cdn.test/chai.png", alt: "A tin" }],
        published_on: ["online-store"],
      });
    });
  });

  it("adds a product listed in no store to a connected one", async () => {
    const asked = await openSignedIn("/stock/products/7", signedIn(), opened([]));

    fireEvent.click(await screen.findByRole("button", { name: "Add to Tea House" }));

    await waitFor(() =>
      expect(
        asked.find((request) => request.method === "post" && request.url?.includes("/stores/"))
          ?.data
      ).toBe(JSON.stringify({ product_id: 7 }))
    );
  });
});
