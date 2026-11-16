import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductDetail } from "@/api/generated/hoard.schemas";
import { productDetail, SETTINGS, stockRow, variation } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const CHAI = productDetail({
  on_hand: "6.000000",
  default_price: "12.000000",
  variations: [
    variation({
      on_hand: "4.000000",
      stock: [stockRow(10, "4.000000")],
    }),
    variation({
      id: 21,
      attributes: { size: "Large" },
      is_default: false,
      price: "15.000000",
      on_hand: "2.000000",
      stock: [stockRow(11, "2.000000")],
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

  describe("by its type", () => {
    /** Each kind's default type, and Bought: bought finished. */
    const typed = {
      ...opened,
      "GET /api/v1/settings/types": {
        status: 200,
        data: [
          { id: 32, kind: "product", name: null, shape: "made", is_default: true },
          { id: 34, kind: "product", name: "Bought", shape: "bought", is_default: false },
        ],
      },
      "GET /api/v1/declarations/product/7": {
        status: 200,
        data: {
          records_own: true,
          recorded_allergens: null,
          ingredient_name: null,
          ingredients: [],
          unweighed: [],
          allergens: [],
          undeclared: ["Chai"],
          recorded_fragrance: null,
          fragrance_recorded: false,
          rinse_off: false,
          fragrance_threshold: "0.001",
          fragrance_shares: {},
          fragrance_allergens: [],
          fragrance_undeclared: [],
        },
      },
    };

    it("shows a made product's recipe, and hides a bought one's", async () => {
      await openSignedIn("/stock/products/7", signedIn(), typed);
      await screen.findByText("Recipe");
      // Neither shape shows what it records of its own until its page is laid out to.
      expect(screen.queryByText("Ingredient name")).toBeNull();
    });

    it("hides a bought product's recipe and what it records of its own", async () => {
      await openSignedIn("/stock/products/7", signedIn(), {
        ...typed,
        "GET /api/v1/products/7": { status: 200, data: { ...CHAI, type_id: 34 } },
      });

      await screen.findByRole("heading", { name: "Chai" });
      await screen.findByText("Bought");
      expect(screen.queryByText("Recipe")).toBeNull();
      expect(screen.queryByText("Ingredient name")).toBeNull();
    });

    it("records what it declares where its type's page shows it", async () => {
      const asked = await openSignedIn("/stock/products/7", signedIn(), {
        ...typed,
        "GET /api/v1/layouts/product": {
          status: 200,
          data: {
            layouts: [
              { kind: "table", is_default: true, hidden: false, definition: {}, updated_at: null },
              {
                kind: "product",
                type_id: 32,
                definition: { main: [{ type: "field", props: { field: "allergens" } }] },
                updated_at: "2026-11-15T10:00:00Z",
              },
            ],
          },
        },
        "PUT /api/v1/declarations/product/7": { status: 200, data: {} },
      });

      fireEvent.click(await screen.findByRole("button", { name: "Edit allergens it records" }));
      const dialog = await screen.findByRole("dialog");
      within(dialog).getByText(
        "A product declares these while it has no recipe, whatever its page shows."
      );
      fireEvent.click(within(dialog).getByLabelText("Its supplier declares its allergens"));
      fireEvent.click(within(dialog).getByLabelText("Milk"));
      fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(
          asked.find((request) => request.method === "put" && request.url?.includes("product/7"))
            ?.data
        ).toBe(
          JSON.stringify({ ingredient_name: null, allergens: ["milk"], fragrance_allergens: null })
        )
      );
    });
  });
});
