import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type {
  StoreFieldDifferenceRead,
  StoreFieldRead,
  StoreRead,
} from "@/api/generated/hoard.schemas";
import { type Answer, buildJob, openSignedIn, signedIn } from "@/testing";

// jsdom does not scroll; the kit's select scrolls its chosen option into view.
Element.prototype.scrollIntoView ??= () => {};

const SYNCED: StoreRead = {
  id: 3,
  channel: "shopify",
  shop_domain: "tea.myshopify.com",
  account_label: "Tea House",
  connected: true,
  last_check_error: "STORE_SCOPES_MISSING: write_inventory",
  state: "synced",
  imported_at: "2026-11-14T09:00:00Z",
  sync_job_id: 8,
  locations: [{ id: 5, external_id: "loc-1", name: "Shop", location_id: 3 }],
  listings: 4,
  linked: 3,
  pushes_queued: 0,
  pushes_failed: 2,
};

const PENDING: StoreRead = {
  ...SYNCED,
  last_check_error: null,
  state: "pending",
  imported_at: null,
  sync_job_id: null,
  locations: [],
};

const FIELD: StoreFieldRead = {
  owner: "product",
  handle: "custom.origin",
  name: "Origin",
  type: "single_line_text_field",
  count: 12,
  entries: [],
  property_type: "text",
  allergens: true,
  ingredients: true,
  suggested: { target: "property", property_id: null, pairs: {} },
  mapping_id: null,
  mapped: null,
};

const FIELDS: StoreFieldRead[] = [
  FIELD,
  {
    ...FIELD,
    handle: "shopify.allergen-information",
    name: "Allergen information",
    type: "list.metaobject_reference",
    count: 4,
    entries: [
      { value: "gid://m/1", name: "Milk" },
      { value: "gid://m/2", name: "Seeds" },
    ],
    property_type: "multi_select",
    ingredients: false,
    suggested: {
      target: "allergens",
      property_id: null,
      pairs: { "gid://m/1": "milk", "gid://m/2": null },
    },
  },
  {
    ...FIELD,
    handle: "custom.weight",
    name: "Weight",
    type: "weight",
    property_type: null,
    allergens: false,
    ingredients: false,
    suggested: null,
  },
];

const PROPERTIES = [
  {
    id: 7,
    kind: "product",
    name: "Region",
    type: "text",
    options: null,
    currency: null,
    counter: null,
  },
];

const answers = (store: StoreRead, more: Record<string, Answer> = {}) => ({
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/settings/locations": {
    status: 200,
    data: [
      { id: 3, name: null, main: true, contact_id: null },
      { id: 4, name: "Back room", main: false, contact_id: null },
    ],
  },
  "GET /api/v1/stores": { status: 200, data: [store] },
  ...more,
});

const admin = signedIn({ page: "settings", admin: true });

/** Opens the choice ``name`` names and picks ``option``. */
async function choose(name: string, option: string) {
  fireEvent.keyDown(await screen.findByRole("combobox", { name }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

describe("StoresSection", () => {
  it("shows how a synced store stands and where its locations sell from", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings" }), answers(SYNCED));

    const store = await screen.findByRole("article", { name: "Tea House" });
    within(store).getByText("Synced");
    within(store).getByText("First synced", { exact: false });
    within(store).getByText("3 of 4 listings sell an item · 2 pushes given up");
    within(store).getByText("The store's app is missing permissions Hoard needs: write_inventory.");
    const shop = within(store).getByText("Shop").closest("tr") as HTMLElement;
    await waitFor(() => expect(shop.textContent).toBe("ShopMain location"));
    expect(within(store).queryByRole("button", { name: "Read the store again" })).toBeNull();
    expect(within(store).queryByRole("button", { name: "Remove sync" })).toBeNull();
  });

  it("lets an admin read a synced store again, and remove its sync once confirmed", async () => {
    const asked = await openSignedIn(
      "/settings",
      admin,
      answers(SYNCED, { "POST /api/v1/stores/3/unsync": { status: 200, data: PENDING } })
    );

    const store = await screen.findByRole("article", { name: "Tea House" });
    within(store).getByRole("button", { name: "Read the store again" });
    within(store).getByRole("combobox");
    fireEvent.click(within(store).getByRole("button", { name: "Remove sync" }));
    const confirm = await screen.findByRole("alertdialog", {
      name: "Remove the sync with Tea House?",
    });
    expect(asked.some((request) => request.url?.endsWith("/unsync"))).toBe(false);
    fireEvent.click(within(confirm).getByRole("button", { name: "Remove sync" }));

    await waitFor(() =>
      expect(
        asked.some((request) => request.method === "post" && request.url?.endsWith("/unsync"))
      ).toBe(true)
    );
  });

  it("walks an admin through syncing a pending store, then queues it", async () => {
    const asked = await openSignedIn(
      "/settings",
      admin,
      answers(PENDING, {
        "GET /api/v1/stores/3/store-locations": {
          status: 200,
          data: [
            { external_id: "loc-1", name: "Shop" },
            { external_id: "loc-2", name: "Warehouse" },
            { external_id: "loc-3", name: "Pop-up" },
          ],
        },
        "GET /api/v1/stores/3/fields": { status: 200, data: FIELDS },
        "GET /api/v1/properties": { status: 200, data: PROPERTIES },
        "POST /api/v1/stores/3/sync": {
          status: 202,
          data: buildJob({ id: 9, kind: "store_sync" }),
        },
      })
    );

    const store = await screen.findByRole("article", { name: "Tea House" });
    within(store).getByText("Connected, not synced");
    expect(within(store).queryByRole("button", { name: "Remove sync" })).toBeNull();
    fireEvent.click(within(store).getByRole("button", { name: "Sync with Shopify" }));

    const wizard = await screen.findByRole("dialog", { name: "Sync with Shopify" });
    within(wizard).getByText("What syncing does");
    within(wizard).getByText(/Nothing is sent to the store/);
    fireEvent.click(within(wizard).getByRole("button", { name: "Next" }));

    within(wizard).getByText("Where each store location's stock goes");
    await waitFor(() =>
      expect(within(wizard).getByRole("combobox", { name: "Shop" }).textContent).toBe(
        "Main location"
      )
    );
    expect(within(wizard).getByRole("combobox", { name: "Warehouse" }).textContent).toBe(
      "A new location: Warehouse"
    );
    await choose("Shop", "Back room");
    await choose("Pop-up", "Nothing");
    fireEvent.click(within(wizard).getByRole("button", { name: "Next" }));

    within(wizard).getByText("What each of the store's fields fills in Hoard");
    await within(wizard).findByText(
      "custom.origin · Product · single_line_text_field · 12 have a value"
    );
    await waitFor(() =>
      expect(within(wizard).getByRole("combobox", { name: "Origin" }).textContent).toBe(
        "A new property: Origin (Text)"
      )
    );
    expect(within(wizard).getByRole("combobox", { name: "Allergen information" }).textContent).toBe(
      "Allergens, from recipes"
    );
    expect(within(wizard).getByRole("combobox", { name: "Weight" }).textContent).toBe(
      "Leave it out"
    );
    await choose("Origin", "Property: Region");
    await choose("Allergen for Seeds", "Sesame");
    fireEvent.click(within(wizard).getByRole("button", { name: "Next" }));

    within(wizard).getByText("After the sync");
    within(wizard).getByText(/come back here and choose Remove sync/);
    fireEvent.click(within(wizard).getByRole("button", { name: "Sync now" }));

    await waitFor(() => {
      const sent = asked.find((request) => request.url?.endsWith("/sync"));
      expect(JSON.parse(String(sent?.data))).toEqual({
        locations: [
          { external_id: "loc-1", location_id: 4 },
          { external_id: "loc-2", new: true },
        ],
        fields: [
          {
            owner: "product",
            handle: "custom.origin",
            target: "property",
            property_id: 7,
            pairs: {},
          },
          {
            owner: "product",
            handle: "shopify.allergen-information",
            target: "allergens",
            property_id: null,
            pairs: { "gid://m/1": "milk", "gid://m/2": "sesame" },
          },
        ],
      });
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("lists differing values and sends Hoard's only when asked", async () => {
    const differing: StoreFieldDifferenceRead[] = [
      {
        mapping_id: 2,
        field: "Allergens",
        target: "allergens",
        owner_id: "p-chai",
        product_id: 4,
        product_name: "Chai",
        hoard: ["Tree nuts"],
        store: ["Milk"],
      },
    ];
    const asked = await openSignedIn(
      "/settings",
      admin,
      answers(
        { ...SYNCED, last_check_error: null },
        {
          "GET /api/v1/stores/3/fields/differing": { status: 200, data: differing },
          "POST /api/v1/stores/3/fields/send": {
            status: 202,
            data: buildJob({ id: 11, kind: "store_fields" }),
          },
          "GET /api/v1/jobs/11": {
            status: 200,
            data: buildJob({
              id: 11,
              kind: "store_fields",
              state: "running",
              step: "store_fields_send",
            }),
          },
        }
      )
    );

    const listed = await screen.findByRole("region", { name: "Differing values" });
    const row = within(listed).getByText("Chai").closest("tr") as HTMLElement;
    expect(row.textContent).toBe("ChaiAllergensTree nutsMilk");
    expect(asked.some((request) => request.url?.endsWith("/fields/send"))).toBe(false);
    fireEvent.click(within(listed).getByRole("button", { name: "Send Hoard's values to Shopify" }));

    await within(listed).findByText("Sending Hoard's values");
    expect(asked.filter((request) => request.url?.endsWith("/fields/send"))).toHaveLength(1);
  });

  it("lets an admin change a synced store's fields", async () => {
    const mapped: StoreFieldRead = {
      ...FIELD,
      mapping_id: 5,
      mapped: { target: "property", property_id: 7, pairs: {} },
    };
    const asked = await openSignedIn(
      "/settings",
      admin,
      answers(
        { ...SYNCED, last_check_error: null },
        {
          "GET /api/v1/stores/3/fields": { status: 200, data: [mapped, FIELDS[1]] },
          "GET /api/v1/properties": { status: 200, data: PROPERTIES },
          "PUT /api/v1/stores/3/fields": {
            status: 200,
            data: buildJob({ id: 12, kind: "store_fields" }),
          },
          "DELETE /api/v1/stores/3/fields/5": { status: 204 },
        }
      )
    );

    const store = await screen.findByRole("article", { name: "Tea House" });
    fireEvent.click(within(store).getByRole("button", { name: "Show the fields" }));
    await waitFor(() =>
      expect(within(store).getByRole("combobox", { name: "Origin" }).textContent).toBe(
        "Property: Region"
      )
    );
    expect(within(store).getByRole("combobox", { name: "Allergen information" }).textContent).toBe(
      "Leave it out"
    );

    await choose("Allergen information", "Allergens, from recipes");
    await waitFor(() => {
      const sent = asked.find((request) => request.method === "put");
      expect(JSON.parse(String(sent?.data))).toEqual({
        owner: "product",
        handle: "shopify.allergen-information",
        target: "allergens",
        property_id: null,
        pairs: { "gid://m/1": "milk", "gid://m/2": null },
      });
    });
    await choose("Origin", "Leave it out");
    await waitFor(() => expect(asked.some((request) => request.method === "delete")).toBe(true));
  });

  it("shows a running sync's progress", async () => {
    const running = buildJob({
      id: 9,
      kind: "store_sync",
      state: "running",
      step: "store_products",
      done: 1200,
      total: 4800,
    });
    await openSignedIn(
      "/settings",
      admin,
      answers(
        { ...PENDING, state: "syncing", sync_job_id: 9 },
        { "GET /api/v1/jobs/9": { status: 200, data: running } }
      )
    );

    const store = await screen.findByRole("article", { name: "Tea House" });
    within(store).getByText("Syncing");
    await within(store).findByText("Products: 1,200 of 4,800");
    expect(within(store).queryByRole("button", { name: "Sync with Shopify" })).toBeNull();
  });

  it("shows why a sync failed, and offers it again", async () => {
    const failed = buildJob({ id: 9, state: "failed", error: "STORE_UNREACHABLE" });
    await openSignedIn(
      "/settings",
      admin,
      answers(
        { ...PENDING, sync_job_id: 9 },
        { "GET /api/v1/jobs/9": { status: 200, data: failed } }
      )
    );

    const store = await screen.findByRole("article", { name: "Tea House" });
    expect((await within(store).findByRole("alert")).textContent).toBe(
      "The store couldn't be reached, and Hoard stopped after trying again. Try again in a moment."
    );
    within(store).getByRole("button", { name: "Sync with Shopify" });
  });
});
