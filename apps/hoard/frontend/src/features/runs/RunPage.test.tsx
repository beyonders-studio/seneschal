import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { RunLineRead, RunRead } from "@/api/generated/hoard.schemas";
import { SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const lot = { id: 4, number: "L-1", expires_on: null, balance: "10.000000" };

const line = (overrides: Partial<RunLineRead> = {}): RunLineRead => ({
  id: 9,
  item: { id: 3, kind: "material", name: "Flour", attributes: {}, archived: false },
  quantity: "4.000000",
  lot: null,
  assumed: false,
  lots: [lot],
  ...overrides,
});

const run = (overrides: Partial<RunRead> = {}): RunRead => ({
  id: 5,
  code: "R-0005",
  item: { id: 8, kind: "base", name: "Dough", attributes: {}, archived: false },
  status: "in_progress",
  location_id: 1,
  initiative_id: 7,
  planned_batches: "2.000000",
  planned_quantity: "8.000000",
  actual_quantity: null,
  assignee_ref: null,
  started_on: "2026-11-02",
  deadline_on: null,
  completed_on: null,
  uncosted: false,
  recipe_id: 2,
  recipe_changed: false,
  labour_minutes: null,
  expires_on: null,
  output_lot: null,
  lines: [line()],
  created_at: "2026-11-01T09:00:00Z",
  created_by: "member-ref",
  updated_at: "2026-11-01T09:00:00Z",
  updated_by: "member-ref",
  ...overrides,
});

const ANSWERS = {
  "GET /api/v1/settings": { status: 200, data: { currency: "USD" } },
  "GET /api/v1/members": { status: 200, data: [] },
};

describe("RunPage", () => {
  it("completes a run with what it made", async () => {
    const asked = await openSignedIn(
      "/production/runs/5",
      signedIn({ page: "production", permissions: ["use", "costs"] }),
      {
        ...ANSWERS,
        "GET /api/v1/runs/5": { status: 200, data: run() },
        "POST /api/v1/runs/5/complete": {
          status: 200,
          data: run({
            status: "completed",
            actual_quantity: "7.000000",
            completed_on: "2026-11-03",
            cost: "8.000000",
            unit_cost: "1.142857",
            output_lot: { id: 6, number: "R-0005", expires_on: null, balance: "7.000000" },
            lines: [
              line({ lot, assumed: true, lots: [], unit_cost: "2.000000", cost: "8.000000" }),
            ],
          }),
        },
      }
    );

    await screen.findByRole("heading", { name: "Run R-0005" });
    fireEvent.click(screen.getByRole("button", { name: "Complete" }));
    const dialog = await screen.findByRole("dialog", { name: "Complete run R-0005" });
    fireEvent.change(within(dialog).getByLabelText("Made"), { target: { value: "7" } });
    fireEvent.change(within(dialog).getByLabelText("Completed on"), {
      target: { value: "2026-11-03" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Complete" }));

    await screen.findByText("Assumed");
    expect(screen.getByText("$8.00", { selector: "dd" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Reopen" })).toBeTruthy();
    const completing = asked.find((request) => request.url?.endsWith("/complete"));
    expect(JSON.parse(completing?.data)).toEqual({
      actual_quantity: "7",
      completed_on: "2026-11-03",
    });
  });

  it("warns of an uncosted run, with no costs for who does not see them", async () => {
    await openSignedIn("/production/runs/5", signedIn({ page: "production" }), {
      ...ANSWERS,
      "GET /api/v1/runs/5": {
        status: 200,
        data: run({ status: "completed", uncosted: true, actual_quantity: "8.000000" }),
      },
    });

    await screen.findByText("This run is uncosted");
    expect(screen.queryByText("Cost of what it consumed")).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Unit cost" })).toBeNull();
  });
});

describe("RunPage, before it is completed", () => {
  it("marks short lines and says when its recipe has changed", async () => {
    await openSignedIn("/production/runs/5", signedIn({ page: "production" }), {
      ...ANSWERS,
      "GET /api/v1/runs/5": {
        status: 200,
        data: run({ recipe_changed: true, lines: [line({ short: "1.500000" })] }),
      },
    });

    await screen.findByText("Its recipe has changed");
    screen.getByText("1.5 short");
  });
});

describe("RunDialog", () => {
  it("plans as much as one ingredient allows, with what is short", async () => {
    const flour = {
      id: 3,
      kind: "material" as const,
      name: "Flour",
      attributes: {},
      archived: false,
    };
    const salt = {
      id: 4,
      kind: "material" as const,
      name: "Salt",
      attributes: {},
      archived: false,
    };
    await openSignedIn("/production/runs", signedIn({ page: "production" }), {
      ...SETTINGS,
      "GET /api/v1/runs": { status: 200, data: { items: [], total: 0 } },
      "GET /api/v1/bases": {
        status: 200,
        data: { items: [{ id: 8, name: "Dough" }], total: 1 },
      },
      "POST /api/v1/runs/preview": {
        status: 200,
        data: {
          batches: "1.5",
          quantity: "1230.000000",
          unit_id: 1,
          lines: [
            { item: flour, unit_id: 1, quantity: "600.000000", usable: "500", short: "100.000000" },
            { item: salt, unit_id: 1, quantity: "300.000000", usable: "300", short: null },
          ],
          most_batches: "1.25",
          use: {
            item_id: 4,
            unit_id: 1,
            amount: "300.000000",
            usable: "300.000000",
            expired: "100.000000",
            most: "250.000000",
          },
        },
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "New run" }));
    const dialog = await screen.findByRole("dialog", { name: "New production run" });
    fireEvent.click(within(dialog).getByRole("combobox", { name: "What it makes" }));
    fireEvent.click(await screen.findByRole("option", { name: /Dough/ }));

    fireEvent.click(
      await within(dialog).findByRole("switch", { name: "Make as much as one ingredient allows" })
    );

    await within(dialog).findByText("100 g short");
    within(dialog).getByText("100 g past its expiry date is left out.", { exact: false });
    within(dialog).getByText("Makes 1,230 g (1.5 batches).");
    within(dialog).getByRole("button", { name: "Make the most there's enough for: 250 g" });
  });
});
