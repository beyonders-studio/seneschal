import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StoreRead } from "@/api/generated/hoard.schemas";
import { openSignedIn, signedIn } from "@/testing";

const STORE: StoreRead = {
  id: 3,
  channel: "shopify",
  shop_domain: "tea.myshopify.com",
  account_label: "Tea House",
  connected: true,
  last_check_error: "STORE_SCOPES_MISSING: write_inventory",
  imported_at: null,
  locations: [{ id: 5, external_id: "loc-1", name: "Shop", location_id: 3 }],
  listings: 4,
  linked: 3,
  pushes_queued: 0,
  pushes_failed: 2,
};

const answers = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/settings/locations": {
    status: 200,
    data: [{ id: 3, name: null, main: true }],
  },
  "GET /api/v1/stores": { status: 200, data: [STORE] },
};

describe("StoresSection", () => {
  it("shows how each store stands and where its locations sell from", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings" }), answers);

    const store = await screen.findByRole("article", { name: "Tea House" });
    within(store).getByText("Connected");
    within(store).getByText("3 of 4 listings sell an item · 2 pushes given up");
    within(store).getByText("The store's app is missing permissions Hoard needs: write_inventory.");
    const shop = within(store).getByText("Shop").closest("tr") as HTMLElement;
    await waitFor(() => expect(shop.textContent).toBe("ShopMain location"));
    expect(within(store).queryByRole("button", { name: "Read the store again" })).toBeNull();
  });

  it("lets an admin read the store again", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), answers);

    const store = await screen.findByRole("article", { name: "Tea House" });
    within(store).getByRole("button", { name: "Read the store again" });
    within(store).getByRole("combobox");
  });
});
