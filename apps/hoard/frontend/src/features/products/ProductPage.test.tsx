import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductDetail } from "@/api/generated/hoard.schemas";
import { productDetail, SETTINGS, stockRow, variation } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

Element.prototype.scrollIntoView ??= () => {};

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

  it("says how a variation is bought: a dozen of it converts by itself", async () => {
    const asked = await openSignedIn("/stock/products/7", signedIn(), {
      ...opened,
      "GET /api/v1/settings/units": {
        status: 200,
        data: [
          { id: 1, name: "g", standard: "gram", measure: "mass", aliases: [] },
          { id: 2, name: "dozen", standard: "dozen", measure: "count", aliases: [] },
        ],
      },
      "GET /api/v1/settings/units/conversion": { status: 200, data: { factor: "12.000000" } },
      "GET /api/v1/contacts": { status: 200, data: [] },
      "POST /api/v1/products/7/variations": { status: 201, data: CHAI },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Add variation" }));
    const dialog = await screen.findByRole("dialog", { name: "Add variation" });
    fireEvent.click(within(dialog).getByRole("combobox", { name: "Bought in" }));
    fireEvent.click(await screen.findByRole("option", { name: /dozen/ }));
    expect(await within(dialog).findByDisplayValue("12.000000")).toHaveProperty("disabled", true);
    fireEvent.change(within(dialog).getByLabelText("Supplier's code"), {
      target: { value: "INF-12" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const sent = asked.find((request) => request.url === "/api/v1/products/7/variations");
      expect(JSON.parse(String(sent?.data))).toMatchObject({
        supplier_code: "INF-12",
        purchase_unit_id: 2,
        purchase_conversion: "12.000000",
      });
    });
    expect(
      asked.find((request) => request.url === "/api/v1/settings/units/conversion")?.params
    ).toEqual({ from_unit_id: 2 });
  });

  describe("at a glance", () => {
    /** What one group of Chai's sales earned. */
    const earned = (revenue: string, profit: string, share: string) => ({
      revenue,
      cost: "0",
      profit,
      share,
      uncosted: 0,
      left_out: 0,
    });

    /** Chai with a SKU, its figures added up, and to a member who sees costs,
     *  what it earned to retail and wholesale customers, its cost, and its
     *  price in the store. */
    const FIGURED: ProductDetail = productDetail({
      ...CHAI,
      totals: {
        consigned: "2.000000",
        committed: "3.000000",
        available: "3.000000",
        potential: null,
        made: "10.000000",
        sold: "8.000000",
        shipped: "5.000000",
        on_order: "12.000000",
        overdue: false,
        profit: {
          revenue: "60.000000",
          cost: "20.000000",
          profit: "40.000000",
          share: "66.67",
          uncosted: 1,
          left_out: 2,
        },
        retail: {
          sold: "5.000000",
          shipped: "3.000000",
          profit: earned("45.000000", "33.000000", "73.33"),
        },
        wholesale: {
          sold: "3.000000",
          shipped: "2.000000",
          profit: earned("15.000000", "-1.000000", "-6.67"),
        },
      },
      variations: [
        variation({
          sku: "CHAI-1",
          on_hand: "6.000000",
          pricing: {
            cost: {
              goods: "2.500000",
              last_known: true,
              labour: null,
              overhead: "0.250000",
              total: "2.750000",
            },
            price: "2.000000",
            store_price: true,
            margin: "-0.750000",
            margin_share: "-37.50",
          },
        }),
      ],
    });

    const figured = { ...opened, "GET /api/v1/products/7": { status: 200, data: FIGURED } };

    it("shows its SKU, stock and figures to date, and no money without costs", async () => {
      await openSignedIn("/stock/products/7", signedIn(), figured);

      await screen.findByText("SKU CHAI-1");
      const tile = (label: string) => screen.getByText(label).closest("div")?.textContent;
      expect(tile("Consigned")).toBe("Consigned2");
      expect(tile("Committed")).toBe("Committed3");
      expect(tile("Sold")).toBe("Sold8");
      expect(tile("Shipped")).toBe("Shipped5");
      expect(tile("On order")).toBe("On order12");
      expect(screen.queryByText("Gross profit")).toBeNull();
      expect(screen.queryByText("Wholesale")).toBeNull();
    });

    it("shows what it earned from retail and wholesale, its cost and its margin", async () => {
      await openSignedIn(
        "/stock/products/7",
        signedIn({ permissions: ["use", SEE_COSTS] }),
        figured
      );

      await screen.findByText("Gross profit");
      const retail = (await screen.findByText("Retail")).closest("tr");
      expect(retail?.textContent).toBe("Retail5$45.00$33.0073.33%");
      const wholesale = screen.getByText("Wholesale").closest("tr");
      expect(wholesale?.textContent).toBe("Wholesale3$15.00-$1.00-6.67%");
      screen.getByText("1 shipment line went out with no cost, so the cost is too low.");
      screen.getByText("2 orders in another currency aren't counted.");
      // Its last known cost, with its overhead, and a loss at the store's price.
      await screen.findByText("Last known");
      screen.getByText("$2.75");
      const price = screen.getByText("Price in the store").closest("div");
      expect(price?.textContent).toBe("Price in the store$2.00");
      const margin = screen.getByText("Margin").closest("div") as HTMLElement;
      within(margin).getByText("Loss");
      expect(margin.textContent).toContain("-$0.75 (-37.5%)");
    });

    it("shows its sales in one row while nobody bought wholesale, and no cost", async () => {
      await openSignedIn("/stock/products/7", signedIn({ permissions: ["use", SEE_COSTS] }), {
        ...figured,
        "GET /api/v1/products/7": {
          status: 200,
          data: productDetail({
            ...FIGURED,
            totals: { ...FIGURED.totals, wholesale: null },
            variations: [
              variation({
                pricing: {
                  cost: null,
                  price: "6.000000",
                  store_price: false,
                  margin: null,
                  margin_share: null,
                },
              }),
            ],
          }),
        },
      });

      const all = (await screen.findByText("All sales")).closest("tr");
      expect(all?.textContent).toBe("All sales5$45.00$33.0073.33%");
      expect(screen.queryByText("Wholesale")).toBeNull();
      await screen.findByText("It has never had a cost, so its margin is unknown.");
      const term = (label: string) =>
        screen.getByText(label, { selector: "dt" }).closest("div")?.textContent;
      expect(term("Price")).toBe("Price$6.00");
      expect(term("Margin")).toBe("MarginUnknown");
    });

    it("counts a bundle's availability from its contents, with no stock of its own", async () => {
      await openSignedIn("/stock/products/7", signedIn(), {
        ...figured,
        "GET /api/v1/products/7": { status: 200, data: { ...FIGURED, bundle: true } },
      });

      await screen.findByText("Committed");
      expect(screen.getByText("Available", { selector: "dt" })).toBeTruthy();
      expect(screen.queryByText("On order")).toBeNull();
      expect(screen.queryByText("Made")).toBeNull();
    });
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
