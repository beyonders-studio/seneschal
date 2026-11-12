import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  orderLine,
  orderLot,
  orderRead,
  orderShipment,
  shipmentLine,
} from "@/features/orders/testing";
import { type Answer, openSignedIn } from "@/testing";

// The dialogs' checkboxes measure themselves, which jsdom has nothing to do with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const ANSWERS: Record<string, Answer> = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/members": { status: 200, data: [] },
  "GET /api/v1/contacts": { status: 200, data: [] },
  "GET /api/v1/holds/reasons": {
    status: 200,
    data: [
      { id: 1, key: "curing", name: null },
      { id: 2, key: "suspect", name: null },
    ],
  },
};

const shipped = orderShipment({
  status: "shipped",
  shipped_on: "2026-11-05",
  lines: [shipmentLine({ lot: { ...orderLot, on_hold: true }, assumed: true, lots: [] })],
});

describe("HoldFromOrder", () => {
  it("holds the lots an order drew as suspect", async () => {
    const asked = await openSignedIn("/orders/5", undefined, {
      ...ANSWERS,
      "GET /api/v1/orders/5": {
        status: 200,
        data: orderRead({
          lines: [orderLine({ shipped: "4.000000" })],
          shipments: [shipped],
        }),
      },
      "POST /api/v1/holds/orders/5": { status: 201, data: [{ id: 6 }] },
    });

    await screen.findByText("C-1");
    screen.getByText("On hold");
    fireEvent.click(screen.getByRole("button", { name: "Hold its lots" }));
    const dialog = await screen.findByRole("dialog", { name: "Hold the lots of O-0005" });
    await waitFor(() =>
      expect(within(dialog).getByRole("combobox", { name: "Reason" }).textContent).toBe("Suspect")
    );
    fireEvent.change(within(dialog).getByLabelText("Notes"), { target: { value: "Sour" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Hold the lots" }));

    await screen.findByText("1 lot put on hold.");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    const placed = asked.find((request) => request.url === "/api/v1/holds/orders/5");
    expect(JSON.parse(placed?.data ?? "")).toEqual({
      reason_id: 2,
      notes: "Sour",
      made_from: false,
    });
  });

  it("is not offered for an order that drew no lot, and shows a short line", async () => {
    await openSignedIn("/orders/5", undefined, {
      ...ANSWERS,
      "GET /api/v1/orders/5": {
        status: 200,
        data: orderRead({ lines: [orderLine({ short: "1.500000" })] }),
      },
    });

    await screen.findByText("Short 1.5");
    screen.getByText("Short");
    expect(screen.queryByRole("button", { name: "Hold its lots" })).toBeNull();
  });
});
