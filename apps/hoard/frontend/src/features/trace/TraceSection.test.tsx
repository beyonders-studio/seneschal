import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SETTINGS } from "@/features/items/testing";
import { lotRead } from "@/features/lots/testing";
import { traceStep } from "@/features/trace/testing";
import { openSignedIn, signedIn } from "@/testing";

const purchase = traceStep({
  kind: "purchase",
  record_id: 4,
  code: "P-4",
  made_lot: null,
  quantity: "10.000000",
  dated_on: "2026-11-01",
  party: { contact_id: 7, name: "Spice Co" },
});
const run = traceStep();
const shipped = traceStep({
  kind: "shipment",
  record_id: 12,
  code: "O-12",
  order_id: 30,
  lot: { id: 8, number: "R-7" },
  from_lot: { id: 8, number: "R-7" },
  made_lot: null,
  party: { contact_id: 9, name: null },
  level: 2,
});
const consumerSale = traceStep({
  kind: "shipment",
  record_id: 13,
  code: "O-13",
  order_id: 31,
  lot: null,
  made_lot: null,
  assumed: true,
});

const open = (back: unknown[], forward: unknown[]) =>
  openSignedIn("/stock/lots/3", signedIn(), {
    ...SETTINGS,
    "GET /api/v1/lots/3": { status: 200, data: lotRead() },
    "GET /api/v1/trace/lots/3": { status: 200, data: { back, forward } },
  });

describe("TraceSection", () => {
  it("lists what went into a lot, where it went, and where it may have gone apart", async () => {
    await open([purchase], [run, shipped, consumerSale]);

    const into = (await screen.findByText("What went into it")).parentElement as HTMLElement;
    const bought = within(into).getByRole("link", { name: "Purchase P-4" });
    expect(bought.getAttribute("href")).toBe("/stock/purchases/4");
    expect(within(into).getByText("Spice Co")).toBeTruthy();

    const went = screen.getByText("Where it went").parentElement as HTMLElement;
    expect(within(went).getByRole("link", { name: "Run R-7" }).getAttribute("href")).toBe(
      "/production/runs/7"
    );
    expect(within(went).getByRole("link", { name: "Order O-12" }).getAttribute("href")).toBe(
      "/orders/30"
    );
    expect(within(went).getByText("A contact Hoard can't read")).toBeTruthy();
    expect(within(went).queryByText("Order O-13")).toBeNull();

    const mayHave = screen.getByText("Where it may have gone").parentElement as HTMLElement;
    expect(within(mayHave).getByRole("link", { name: "Order O-13" })).toBeTruthy();
    expect(within(mayHave).getByText("A consumer")).toBeTruthy();
    expect(within(mayHave).getByText("May have")).toBeTruthy();
    expect(within(mayHave).getByRole("link", { name: "L-3" }).getAttribute("href")).toBe(
      "/stock/lots/3"
    );

    expect(screen.getByRole("link", { name: "Recall this lot" }).getAttribute("href")).toBe(
      "/stock/recall?lot=3"
    );
  });

  it("says when nothing went into the lot or came from it", async () => {
    await open([], []);

    await screen.findByText("Nothing went into this lot or came from it yet.");
    expect(screen.queryByText("Where it may have gone")).toBeNull();
  });
});
