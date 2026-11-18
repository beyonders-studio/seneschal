import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { baseDetail, productDetail, SETTINGS, variation } from "@/features/items/testing";
import { type Answer, openSignedIn, signedIn } from "@/testing";

// The kit's options scroll themselves into view, which jsdom cannot.
Element.prototype.scrollIntoView ??= () => {};

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

  it("sets whether a run makes a base first", async () => {
    const kit = baseDetail({ name: "Packaging kit" });
    const asked = await openSignedIn("/stock/bases/8", signedIn(), {
      ...SETTINGS,
      "GET /api/v1/bases/8": { status: 200, data: kit },
      "GET /api/v1/recipes": { status: 200, data: { items: [], total: 0 } },
      "PUT /api/v1/bases/8": { status: 200, data: { ...kit, auto_manufacture: "out_of_stock" } },
    });

    const own = await screen.findByRole("combobox", { name: "Runs that use it" });
    expect(own.textContent).toBe("Make nothing");
    await choose("Runs that use it", "Make only what a run is short of");
    await waitFor(() => expect(sent(asked, "/api/v1/bases/8")).toBeDefined());
    expect(JSON.parse(sent(asked, "/api/v1/bases/8")?.data ?? "")).toMatchObject({
      name: "Packaging kit",
      tracking_unit_id: 1,
      auto_manufacture: "out_of_stock",
    });
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Runs that use it" }).textContent).toBe(
        "Make only what a run is short of"
      )
    );
  });

  it("says why a base that cures isn't made by the runs that use it", async () => {
    await openSignedIn("/stock/bases/8", signedIn(), {
      ...SETTINGS,
      "GET /api/v1/bases/8": { status: 200, data: baseDetail({ cure_days: 3 }) },
      "GET /api/v1/recipes": { status: 200, data: { items: [], total: 0 } },
      "PUT /api/v1/bases/8": { status: 409, data: { detail: "AUTO_MANUFACTURE_CURES" } },
    });

    await choose("Runs that use it", "Always make what a run uses");
    await screen.findByText(/that cures can't be made by the runs that use it/);
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
    // Taking what holds above, it still offers a choice of its own.
    await waitFor(() =>
      expect(screen.getByRole("combobox", { name: "Store orders" }).textContent).toBe(
        "As your settings say: Make nothing"
      )
    );
  });
});
