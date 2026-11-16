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
  state: "synced",
  imported_at: "2026-11-14T09:00:00Z",
  sync_job_id: null,
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
  product_type: null,
  vendor: null,
  tags: [],
  updated_at: "2026-11-14T09:00:00Z",
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
  it("edits the store's product from its copy, with its price for a member who sees costs", async () => {
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
        updated_at: "2026-11-14T09:00:00Z",
        title: "Masala chai",
        description: "<p>Spiced</p>",
        status: "active",
        variants: [{ id: "v1", sku: "CHAI", price: "14" }],
        images: [{ id: "img-1", url: "https://cdn.test/chai.png", alt: "A tin" }],
        published_on: ["online-store"],
      });
    });
  });

  it("shows the store's newer version when the product changed there", async () => {
    const read = { status: 200, data: HELD };
    await openSignedIn("/stock/products/7", signedIn(), {
      ...opened([LISTING]),
      "GET /api/v1/stores/3/products/p1": read,
      "PUT /api/v1/stores/3/products/p1": {
        status: 409,
        data: { detail: "STORE_PRODUCT_CHANGED" },
      },
    });
    const store = await screen.findByRole("region", { name: "Tea House" });
    fireEvent.change(await within(store).findByLabelText("Title in the store"), {
      target: { value: "Masala chai" },
    });
    // Meanwhile, the product changed in the store.
    read.data = { ...HELD, title: "Chai tea", updated_at: "2026-11-15T09:00:00Z" };

    fireEvent.click(within(store).getByRole("button", { name: "Save to the store" }));

    await within(store).findByText(/This product changed in Shopify since you opened it/);
    await waitFor(() =>
      expect((within(store).getByLabelText("Title in the store") as HTMLInputElement).value).toBe(
        "Chai tea"
      )
    );
  });

  it("adds a product to no store that isn't synced", async () => {
    await openSignedIn("/stock/products/7", signedIn(), {
      ...opened([]),
      "GET /api/v1/stores": { status: 200, data: [{ ...STORE, state: "pending" }] },
    });

    await screen.findByText("This product isn't listed in a connected store.");
    expect(screen.queryByRole("button", { name: "Add to Tea House" })).toBeNull();
  });

  it("adds a product listed in no store to a synced one", async () => {
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
