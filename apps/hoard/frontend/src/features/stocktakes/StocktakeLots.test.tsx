import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { StocktakeRead, StocktakeRowRead } from "@/api/generated/hoard.schemas";
import { cameraReads, openSignedIn, signedIn } from "@/testing";

const flour = { id: 4, kind: "material" as const, name: "Flour", attributes: {}, archived: false };

const row = (
  id: number,
  lot: string | null,
  overrides: Partial<StocktakeRowRead> = {}
): StocktakeRowRead => ({
  id,
  item: flour,
  lot: lot === null ? null : { id: id * 10, number: lot, expires_on: null },
  on_hand: "5.000000",
  expected: null,
  counted: null,
  ...overrides,
});

const counting = (
  rows: StocktakeRowRead[],
  overrides: Partial<StocktakeRead> = {}
): StocktakeRead => ({
  id: 3,
  kind: "material",
  location_id: 1,
  category_id: null,
  sample_size: null,
  lot_count: "full",
  dated_on: "2026-11-05",
  notes: null,
  row_count: rows.length,
  confirmed_count: 0,
  accuracy: null,
  finished: false,
  created_at: "2026-11-05T09:00:00Z",
  created_by: "member-ref",
  rows,
  ...overrides,
});

const ANSWERS = { "GET /api/v1/members": { status: 200, data: [] } };

describe("A stocktake that counts lots", () => {
  it("names each row's lot, and finishes a full count once asked", async () => {
    const asked = await openSignedIn("/stock/stocktakes/3", signedIn(), {
      ...ANSWERS,
      "GET /api/v1/stocktakes/3": {
        status: 200,
        data: counting([row(11, "L-1"), row(12, null, { on_hand: "2.000000" })]),
      },
      "POST /api/v1/stocktakes/3/finish": {
        status: 200,
        data: counting([row(11, "L-1")], { finished: true }),
      },
    });

    await screen.findByLabelText("Counted of Flour · L-1");
    screen.getByLabelText("Counted of Flour · not in a lot");
    fireEvent.click(screen.getByRole("button", { name: "Finish the count" }));
    const dialog = await screen.findByRole("alertdialog");
    fireEvent.click(within(dialog).getByRole("button", { name: "Finish the count" }));

    await waitFor(() =>
      expect(asked.some((config) => config.url === "/api/v1/stocktakes/3/finish")).toBe(true)
    );
  });

  it("adds a lot found while counting when its label is scanned, ready to count", async () => {
    cameraReads("L-9");
    const asked = await openSignedIn("/stock/stocktakes/3", signedIn(), {
      ...ANSWERS,
      "GET /api/v1/stocktakes/3": { status: 200, data: counting([row(11, "L-1")]) },
      "POST /api/v1/scans": {
        status: 200,
        data: {
          kind: "plain",
          text: "L-9",
          gtin: null,
          lot: null,
          serial: null,
          sscc: null,
          expires_on: null,
          best_before: null,
          produced_on: null,
          count: null,
          items: [],
          lots: [
            {
              id: 190,
              item_id: 4,
              location_id: 1,
              number: "L-9",
              expires_on: null,
              balance: "5",
              on_hold: false,
              loose: false,
              path: "/stock/lots/190",
            },
          ],
          problems: [],
        },
      },
      "POST /api/v1/stocktakes/3/rows": {
        status: 200,
        data: counting([
          row(11, "L-1"),
          row(19, null, { lot: { id: 190, number: "L-9", expires_on: null } }),
        ]),
      },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Scan to count" }));

    const field = await screen.findByLabelText("Counted of Flour · L-9");
    await waitFor(() => expect(document.activeElement).toBe(field));
    const added = asked.find((config) => config.url === "/api/v1/stocktakes/3/rows");
    expect(JSON.parse(added?.data)).toEqual({ lot_id: 190 });
  });
});
