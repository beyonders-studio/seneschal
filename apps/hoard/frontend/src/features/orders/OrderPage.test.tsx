import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  orderLine,
  orderLot,
  orderRead,
  orderShipment,
  shipmentLine,
} from "@/features/orders/testing";
import { openSignedIn } from "@/testing";

// The dialogs' checkboxes measure themselves, which jsdom has nothing to do with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const ANSWERS = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/members": { status: 200, data: [] },
  "GET /api/v1/contacts": {
    status: 200,
    data: [{ id: 4, name: "Corner Cafe", is_organization: true }],
  },
};

const shipped = orderShipment({
  status: "shipped",
  shipped_on: "2026-11-05",
  lines: [shipmentLine({ lot: orderLot, assumed: true, lots: [] })],
});

describe("OrderPage", () => {
  it("plans a shipment of what is left, and ships it", async () => {
    const asked = await openSignedIn("/orders/5", undefined, {
      ...ANSWERS,
      "GET /api/v1/orders/5": { status: 200, data: orderRead() },
      "POST /api/v1/shipments": {
        status: 201,
        data: orderRead({
          lines: [orderLine({ held: "3.000000" })],
          shipments: [orderShipment({ lines: [shipmentLine({ quantity: "3.000000" })] })],
        }),
      },
      "POST /api/v1/shipments/12/ship": {
        status: 200,
        data: orderRead({
          lines: [orderLine({ held: "3.000000", shipped: "3.000000" })],
          shipments: [shipped],
        }),
      },
    });

    await screen.findByRole("heading", { name: /Order O-0005/ });
    await screen.findByText(/Corner Cafe/);
    fireEvent.click(screen.getByRole("button", { name: "New shipment" }));
    const plan = await screen.findByRole("dialog", { name: "New shipment" });
    const sending = within(plan).getByLabelText("Chai");
    expect((sending as HTMLInputElement).value).toBe("4");
    fireEvent.change(sending, { target: { value: "3" } });
    fireEvent.click(within(plan).getByRole("button", { name: "Save" }));

    await screen.findByText("Shipment 1");
    const planning = asked.find((request) => request.url === "/api/v1/shipments");
    expect(JSON.parse(planning?.data)).toMatchObject({
      order_id: 5,
      location_id: 1,
      lines: [{ order_line_id: 9, quantity: "3" }],
    });

    fireEvent.click(screen.getByRole("button", { name: "Ship" }));
    const ship = await screen.findByRole("dialog", { name: "Ship it" });
    fireEvent.change(within(ship).getByLabelText("Shipped on"), {
      target: { value: "2026-11-05" },
    });
    fireEvent.click(within(ship).getByRole("button", { name: "Ship" }));

    await screen.findByText("Assumed");
    expect(screen.getByText("3 of 4")).toBeTruthy();
    const shipping = asked.find((request) => request.url?.endsWith("/ship"));
    expect(JSON.parse(shipping?.data)).toEqual({ shipped_on: "2026-11-05" });
  });

  it("records what came back damaged", async () => {
    const asked = await openSignedIn("/orders/5", undefined, {
      ...ANSWERS,
      "GET /api/v1/orders/5": {
        status: 200,
        data: orderRead({ status: "shipped", shipments: [shipped] }),
      },
      "POST /api/v1/shipments/12/lines/11/returns": {
        status: 201,
        data: orderRead({
          status: "shipped",
          shipments: [
            {
              ...shipped,
              lines: [
                shipmentLine({
                  lot: orderLot,
                  lots: [],
                  returns: [{ id: 2, quantity: "1.000000", dated_on: "2026-11-07", damaged: true }],
                }),
              ],
            },
          ],
        }),
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Return" }));
    const dialog = await screen.findByRole("dialog", { name: "Something came back" });
    fireEvent.change(within(dialog).getByLabelText("Quantity"), { target: { value: "1" } });
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2026-11-07" } });
    fireEvent.click(within(dialog).getByLabelText("It came back damaged"));
    within(dialog).getByText(/written off as returned damaged/);
    fireEvent.click(within(dialog).getByRole("button", { name: "Record it" }));

    await screen.findByText("Damaged");
    const recording = asked.find((request) => request.url?.endsWith("/returns"));
    expect(JSON.parse(recording?.data)).toEqual({
      quantity: "1",
      dated_on: "2026-11-07",
      damaged: true,
    });
  });

  it("keeps a store's order's lines as the store has them", async () => {
    await openSignedIn("/orders/5", undefined, {
      ...ANSWERS,
      "GET /api/v1/orders/5": {
        status: 200,
        data: orderRead({
          store_id: 1,
          external_id: "gid://order/1",
          external_number: "#1001",
          customer_contact_id: null,
        }),
      },
    });

    await screen.findByText(/Store order #1001/);
    expect(screen.queryByRole("button", { name: "Add item" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
    expect(screen.getByRole("button", { name: "Cancel order" })).toBeTruthy();
  });
});
