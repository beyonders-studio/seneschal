import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import {
  orderLine,
  orderLot,
  orderRead,
  orderShipment,
  shipmentLine,
} from "@/features/orders/testing";
import { cameraReads, openSignedIn, signedIn } from "@/testing";

// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const ANSWERS = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/members": { status: 200, data: [] },
  "GET /api/v1/contacts": { status: 200, data: [] },
};

const LABEL = "(01)09506000134352(10)C-1";

const lotCell = () => screen.getByRole("combobox", { name: "Lot" });

describe("Scanning lots onto a shipment", () => {
  beforeEach(() => cameraReads(LABEL));

  it("places the scanned lot on its line and says so", async () => {
    const scanned = orderRead({
      shipments: [
        orderShipment({
          lines: [shipmentLine({ lot: orderLot, lot_source: "scanned" })],
        }),
      ],
    });
    const asked = await openSignedIn("/orders/5", signedIn(), {
      ...ANSWERS,
      "GET /api/v1/orders/5": {
        status: 200,
        data: orderRead({ lines: [orderLine()], shipments: [orderShipment()] }),
      },
      "POST /api/v1/shipments/12/scans": {
        status: 200,
        data: { order: scanned, line_id: 11, lot_id: 4, split: false, not_first: false },
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Scan a lot" }));

    await screen.findByText("Scanned C-1 for Chai.");
    const scan = asked.find((config) => config.url === "/api/v1/shipments/12/scans");
    expect(JSON.parse(scan?.data)).toEqual({ text: LABEL });
    within(lotCell().parentElement as HTMLElement).getByText("Scanned");
  });

  it("leaves choosing by hand to admins where a category requires a scan", async () => {
    const required = orderRead({
      shipments: [orderShipment({ lines: [shipmentLine({ lot_scan: "required" })] })],
    });
    await openSignedIn("/orders/5", signedIn(), {
      ...ANSWERS,
      "GET /api/v1/orders/5": { status: 200, data: required },
    });

    await waitFor(() => expect(lotCell()).toHaveProperty("disabled", true));
    screen.getByText("Scan its label to choose its lot.");
  });

  it("lets an admin choose by hand where a scan is required", async () => {
    const required = orderRead({
      shipments: [orderShipment({ lines: [shipmentLine({ lot_scan: "required" })] })],
    });
    await openSignedIn("/orders/5", signedIn({ admin: true }), {
      ...ANSWERS,
      "GET /api/v1/orders/5": { status: 200, data: required },
    });

    await waitFor(() => expect(lotCell()).toHaveProperty("disabled", false));
  });

  it("notes a lot chosen by hand where its category warns", async () => {
    const warned = orderRead({
      shipments: [
        orderShipment({
          lines: [shipmentLine({ lot: orderLot, lot_source: "chosen", lot_scan: "warn" })],
        }),
      ],
    });
    await openSignedIn("/orders/5", signedIn(), {
      ...ANSWERS,
      "GET /api/v1/orders/5": { status: 200, data: warned },
    });

    await screen.findByText("Chosen by hand, not scanned.");
  });
});
