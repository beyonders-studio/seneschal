import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { orderRead } from "@/features/orders/testing";
import { openSignedIn } from "@/testing";

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
});
