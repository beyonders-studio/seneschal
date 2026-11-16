import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { PurchaseLineRead, PurchaseRead } from "@/api/generated/hoard.schemas";
import { material, product, productDetail, SETTINGS, variation } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

const line = (overrides: Partial<PurchaseLineRead> = {}): PurchaseLineRead => ({
  id: 9,
  item: { id: 3, kind: "material", name: "Flour", attributes: {}, archived: false },
  sku: "FL-1",
  category_id: 2,
  purchase_quantity: "1.000000",
  tracking_quantity: "453.592000",
  unit_cost: "2.000000",
  landed_cost: "0.004409",
  received_on: null,
  lot: null,
  ...overrides,
});

const purchase = (lines: PurchaseLineRead[]): PurchaseRead => ({
  id: 5,
  code: "P-0005",
  dated_on: "2026-11-01",
  supplier_contact_id: null,
  location_id: 1,
  paid: false,
  expected_on: "2026-11-03",
  overdue: true,
  notes: "Leave at the back door",
  discount: "0.000000",
  shipping: "0.000000",
  tax: "0.000000",
  total: "20.000000",
  lines,
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
});

describe("PurchasePage", () => {
  it("receives part of a line on the day it arrived", async () => {
    const asked = await openSignedIn(
      "/stock/purchases/5",
      signedIn({ permissions: ["use", "costs"] }),
      {
        "GET /api/v1/purchases/5": { status: 200, data: purchase([line()]) },
        "POST /api/v1/purchases/5/lines/9/receive": {
          status: 200,
          data: purchase([
            line({ purchase_quantity: "0.559075", tracking_quantity: "253.592000" }),
            line({
              id: 10,
              purchase_quantity: "0.440925",
              tracking_quantity: "200.000000",
              received_on: "2026-11-04",
              lot: { id: 4, number: "L-7", expires_on: null, notes: null },
            }),
          ]),
        },
        ...SETTINGS,
        "GET /api/v1/contacts": { status: 200, data: [] },
        "GET /api/v1/members": { status: 200, data: [] },
      }
    );

    await screen.findByRole("heading", { name: "Purchase P-0005 Overdue" });
    await screen.findByText("$20.00");
    screen.getByText("Leave at the back door");
    screen.getByText(/Expected Nov 3, 2026/);
    screen.getByRole("cell", { name: "FL-1" });
    await screen.findByRole("cell", { name: "Tea" });
    fireEvent.click(screen.getByRole("button", { name: "Receive" }));
    const dialog = await screen.findByRole("dialog", { name: "Receive line" });
    fireEvent.change(within(dialog).getByLabelText("Received on"), {
      target: { value: "2026-11-04" },
    });
    fireEvent.change(within(dialog).getByLabelText("Quantity received"), {
      target: { value: "200" },
    });
    fireEvent.change(within(dialog).getByLabelText("Lot number"), { target: { value: "L-7" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Receive" }));

    await screen.findByRole("cell", { name: "L-7" });
    screen.getByRole("cell", { name: "Nov 4, 2026" });
    expect(screen.getAllByRole("button", { name: "Receive" })).toHaveLength(1);
    expect(screen.queryByRole("dialog")).toBeNull();
    const receive = asked.find((request) => request.url?.endsWith("/receive"));
    expect(JSON.parse(receive?.data)).toEqual({
      received_on: "2026-11-04",
      quantity: "200",
      number: "L-7",
    });
  });

  it("buys a product as a material is bought, and never a bundle", async () => {
    const asked = await openSignedIn(
      "/stock/purchases/5",
      signedIn({ permissions: ["use", "costs"] }),
      {
        "GET /api/v1/purchases/5": { status: 200, data: purchase([]) },
        "GET /api/v1/materials": { status: 200, data: { items: [material()], total: 1 } },
        "GET /api/v1/products": {
          status: 200,
          data: {
            items: [
              product({ name: "Infuser" }),
              product({ id: 8, name: "Gift box", bundle: true }),
            ],
            total: 2,
          },
        },
        "GET /api/v1/products/7": {
          status: 200,
          data: productDetail({ name: "Infuser", variations: [variation()] }),
        },
        "POST /api/v1/purchases/5/lines": { status: 201, data: purchase([]) },
        ...SETTINGS,
        "GET /api/v1/contacts": { status: 200, data: [] },
        "GET /api/v1/members": { status: 200, data: [] },
      }
    );

    fireEvent.click(await screen.findByRole("button", { name: "Add a line" }));
    const dialog = await screen.findByRole("dialog", { name: "Add a line" });
    fireEvent.click(within(dialog).getByRole("combobox", { name: "Material or product" }));
    await screen.findByRole("option", { name: /Oolong/ });
    expect(screen.queryByRole("option", { name: /Gift box/ })).toBeNull();
    fireEvent.click(screen.getByRole("option", { name: /Infuser/ }));
    fireEvent.change(within(dialog).getByLabelText("Quantity bought"), {
      target: { value: "12" },
    });
    await waitFor(() =>
      expect(asked.some((request) => request.url === "/api/v1/products/7")).toBe(true)
    );
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => {
      const sent = asked.find((request) => request.url === "/api/v1/purchases/5/lines");
      expect(JSON.parse(sent?.data)).toEqual({
        item_id: 20,
        purchase_quantity: "12",
        unit_cost: "0",
      });
    });
  });

  it("moves a received line's day, and the day the purchase is expected", async () => {
    const received = line({
      received_on: "2026-11-04",
      lot: { id: 4, number: "L-7", expires_on: null, notes: null },
    });
    const asked = await openSignedIn(
      "/stock/purchases/5",
      signedIn({ permissions: ["use", "costs"] }),
      {
        "GET /api/v1/purchases/5": { status: 200, data: purchase([received]) },
        "PATCH /api/v1/purchases/5/lines/9": { status: 200, data: purchase([received]) },
        "PATCH /api/v1/purchases/5": { status: 200, data: purchase([received]) },
        ...SETTINGS,
        "GET /api/v1/contacts": { status: 200, data: [] },
        "GET /api/v1/members": { status: 200, data: [] },
      }
    );

    fireEvent.keyDown(await screen.findByRole("button", { name: "Actions for Flour" }), {
      key: "Enter",
    });
    fireEvent.click(await screen.findByRole("menuitem", { name: "Edit" }));
    const editLine = await screen.findByRole("dialog", { name: "Edit line" });
    fireEvent.change(within(editLine).getByLabelText("Received on"), {
      target: { value: "2026-11-06" },
    });
    fireEvent.click(within(editLine).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    fireEvent.click(screen.getByRole("button", { name: "Edit" }));
    const edit = await screen.findByRole("dialog", { name: "Edit purchase" });
    fireEvent.change(within(edit).getByLabelText("Expected"), { target: { value: "" } });
    fireEvent.change(within(edit).getByLabelText("Notes"), { target: { value: " " } });
    fireEvent.click(within(edit).getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    const [moved, changed] = asked.filter((request) => request.method === "patch");
    expect(JSON.parse(moved?.data)).toEqual({ received_on: "2026-11-06", unit_cost: "2.000000" });
    expect(JSON.parse(changed?.data)).toMatchObject({ expected_on: null, notes: null });
  });
});
