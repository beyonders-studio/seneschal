import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ProductVariationRead } from "@/api/generated/hoard.schemas";
import { productDetail, SETTINGS } from "@/features/items/testing";
import { type Answer, openSignedIn, signedIn } from "@/testing";

// The kit's options scroll themselves into view, which jsdom cannot.
Element.prototype.scrollIntoView ??= () => {};

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
  auto_manufacture: "always",
  variations: [
    variation({}),
    variation({ id: 21, attributes: { size: "Large" }, is_default: false, sku: "CH-L" }),
  ],
});

const OPENED: Record<string, Answer> = {
  ...SETTINGS,
  "GET /api/v1/products/7": { status: 200, data: CHAI },
  "GET /api/v1/products/7/images": { status: 200, data: [] },
};

/** Opens the choice ``name`` names and picks ``option``. */
async function choose(name: string, option: string) {
  fireEvent.keyDown(await screen.findByRole("combobox", { name }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

const sent = (asked: { method?: string; url?: string; data?: string }[], url: string) =>
  asked.find((request) => request.method === "put" && request.url === url);

describe("AutoManufactureField", () => {
  it("lets an admin choose what store orders make", async () => {
    const asked = await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      ...SETTINGS,
      "GET /api/v1/settings/reasons": { status: 200, data: [] },
      "GET /api/v1/properties": { status: 200, data: [] },
      "PUT /api/v1/settings": {
        status: 200,
        data: { currency: "USD", auto_manufacture: "always" },
      },
    });

    await choose("Store orders", "Make only what is out of stock");

    await waitFor(() => expect(sent(asked, "/api/v1/settings")).toBeDefined());
    expect(JSON.parse(sent(asked, "/api/v1/settings")?.data ?? "")).toEqual({
      auto_manufacture: "out_of_stock",
    });
  });

  it("shows a member the organization's choice without a way to change it", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings" }), {
      ...SETTINGS,
      "GET /api/v1/settings/reasons": { status: 200, data: [] },
    });

    const choice = await screen.findByRole("combobox", { name: "Store orders" });
    expect(choice.textContent).toBe("Make nothing");
    expect(choice).toHaveProperty("disabled", true);
  });

  it("sets a product's and each variation's own, or takes what holds above", async () => {
    const asked = await openSignedIn("/stock/products/7", signedIn(), {
      ...OPENED,
      "PUT /api/v1/products/7": { status: 200, data: { ...CHAI, auto_manufacture: null } },
      "PUT /api/v1/products/7/variations/21": { status: 200, data: CHAI },
    });

    const own = await screen.findByRole("combobox", { name: "Store orders" });
    expect(own.textContent).toBe("Always make what is sold");
    const large = screen.getByRole("combobox", { name: "Large" });
    expect(large.textContent).toBe("As the product says: Always make what is sold");

    await choose("Large", "Make nothing");
    await waitFor(() => expect(sent(asked, "/api/v1/products/7/variations/21")).toBeDefined());
    expect(JSON.parse(sent(asked, "/api/v1/products/7/variations/21")?.data ?? "")).toEqual({
      attributes: { size: "Large" },
      sku: "CH-L",
      auto_manufacture: "off",
    });

    await choose("Store orders", "As your settings say: Make nothing");
    await waitFor(() => expect(sent(asked, "/api/v1/products/7")).toBeDefined());
    expect(JSON.parse(sent(asked, "/api/v1/products/7")?.data ?? "")).toMatchObject({
      name: CHAI.name,
      auto_manufacture: null,
    });
  });
});
