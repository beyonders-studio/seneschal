import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { orderRead } from "@/features/orders/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

// The dialog's checkbox measures itself, which jsdom has nothing to do with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const CONTACTS = [{ id: 4, name: "Corner Cafe", is_organization: true }];

describe("OrdersPage", () => {
  it("lists hand orders by customer and store orders by number", async () => {
    await openSignedIn("/orders", undefined, {
      "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
      "GET /api/v1/contacts": { status: 200, data: CONTACTS },
      "GET /api/v1/orders": {
        status: 200,
        data: {
          items: [
            orderRead({ paid: true }),
            orderRead({
              id: 6,
              code: "O-0006",
              store_id: 1,
              external_number: "#1001",
              customer_contact_id: null,
              status: "pending",
            }),
          ],
          total: 2,
        },
      },
    });

    await screen.findByRole("cell", { name: "Corner Cafe" });
    expect(screen.getByRole("cell", { name: "Store order #1001" })).toBeTruthy();
    expect(screen.getByText("Unpaid")).toBeTruthy();
    expect(screen.getByText("Paid")).toBeTruthy();
  });

  it("lists a store customer's orders by their number", async () => {
    const asked = await openSignedIn("/orders", undefined, {
      "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
      "GET /api/v1/contacts": { status: 200, data: CONTACTS },
      "GET /api/v1/orders": { status: 200, data: { items: [orderRead()], total: 1 } },
    });

    fireEvent.change(await screen.findByRole("searchbox", { name: "Customer number" }), {
      target: { value: " 99 " },
    });

    await waitFor(() =>
      expect(
        asked.some(
          (request) => request.url === "/api/v1/orders" && request.params?.store_customer === "99"
        )
      ).toBe(true)
    );
  });

  it("adds an order for a customer among the community's contacts", async () => {
    const asked = await openSignedIn("/orders", undefined, {
      "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
      "GET /api/v1/members": { status: 200, data: [] },
      "GET /api/v1/contacts": { status: 200, data: CONTACTS },
      "GET /api/v1/orders": { status: 200, data: { items: [], total: 0 } },
      "POST /api/v1/orders": { status: 201, data: orderRead({ lines: [] }) },
      "GET /api/v1/orders/5": { status: 200, data: orderRead({ lines: [] }) },
    });

    fireEvent.click(await screen.findByRole("button", { name: "New order" }));
    const dialog = await screen.findByRole("dialog", { name: "New order" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await screen.findByText("Choose the customer the order is for.");
    expect(
      asked.some((request) => request.method === "post" && request.url === "/api/v1/orders")
    ).toBe(false);

    fireEvent.click(await within(dialog).findByRole("combobox", { name: "Customer" }));
    fireEvent.click(await screen.findByRole("option", { name: "Corner Cafe" }));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await screen.findByRole("heading", { name: /Order O-0005/ });
    const adding = asked.find(
      (request) => request.method === "post" && request.url === "/api/v1/orders"
    );
    expect(JSON.parse(adding?.data)).toMatchObject({ customer_contact_id: 4, paid: false });
  });

  it("sets the price point a customer buys at, for a member who sees costs", async () => {
    const asked = await openSignedIn("/orders", signedIn({ permissions: ["use", SEE_COSTS] }), {
      "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
      "GET /api/v1/members": { status: 200, data: [] },
      "GET /api/v1/contacts": { status: 200, data: CONTACTS },
      "GET /api/v1/orders": { status: 200, data: { items: [], total: 0 } },
      "GET /api/v1/settings/price-points": {
        status: 200,
        data: [{ id: 2, name: "Wholesale", markup: "50", listing_fee: "0", commission: "0" }],
      },
      "GET /api/v1/contacts/4/price-point": { status: 200, data: { price_point_id: null } },
      "PUT /api/v1/contacts/4/price-point": { status: 200, data: { price_point_id: 2 } },
    });

    fireEvent.click(await screen.findByRole("button", { name: "New order" }));
    const dialog = await screen.findByRole("dialog", { name: "New order" });
    expect(within(dialog).queryByText("Price point")).toBeNull();
    fireEvent.click(await within(dialog).findByRole("combobox", { name: "Customer" }));
    fireEvent.click(await screen.findByRole("option", { name: "Corner Cafe" }));
    const point = await within(dialog).findByRole("combobox", { name: "Price point" });
    within(point).getByText("Default prices");
    fireEvent.click(point);
    fireEvent.click(await screen.findByRole("option", { name: "Wholesale" }));

    await waitFor(() => {
      const sent = asked.find((request) => request.method === "put");
      expect(JSON.parse(String(sent?.data))).toEqual({ price_point_id: 2 });
    });
  });
});
