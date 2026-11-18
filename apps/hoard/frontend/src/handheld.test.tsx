import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ScanRead } from "@/api/generated/hoard.schemas";
import { buildResults } from "@/features/search/testing";
import { openSignedIn, signedIn } from "@/testing";

const LABEL = "(01)09506000134352(10)C-1";

const scanned: ScanRead = {
  kind: "gs1",
  text: LABEL,
  gtin: "09506000134352",
  lot: "C-1",
  serial: null,
  sscc: null,
  expires_on: null,
  best_before: null,
  produced_on: null,
  count: null,
  items: [],
  lots: [
    {
      id: 4,
      item_id: 7,
      location_id: 1,
      number: "C-1",
      expires_on: null,
      balance: "5",
      on_hold: false,
      loose: false,
      path: "/stock/lots/4",
    },
  ],
  problems: [],
};

const ANSWERS = {
  "POST /api/v1/scans": { status: 200, data: scanned },
  "GET /api/v1/search/suggest": { status: 200, data: { items: [], exact_match: false } },
  "GET /api/v1/search": { status: 200, data: buildResults() },
};

/** A handheld scanner in keyboard mode: every key at once, then Enter. */
function scanWithHandheld(text: string, into: Element = document.body) {
  for (const key of text) fireEvent.keyDown(into, { key });
  fireEvent.keyDown(into, { key: "Enter" });
}

describe("A handheld scanner", () => {
  it("opens what a scan names, from any page", async () => {
    const asked = await openSignedIn("/reports", signedIn(), ANSWERS);
    await screen.findByRole("combobox", { name: "Search Hoard" });

    scanWithHandheld(LABEL);

    await waitFor(() => expect(asked.map((config) => config.url)).toContain("/api/v1/lots/4"));
    const scan = asked.find((config) => config.url === "/api/v1/scans");
    expect(JSON.parse(scan?.data)).toEqual({ text: LABEL });
  });

  it("isn't someone typing", async () => {
    const asked = await openSignedIn("/reports", signedIn(), ANSWERS);
    await screen.findByRole("combobox", { name: "Search Hoard" });
    let now = 0;
    vi.spyOn(Date, "now").mockImplementation(() => {
      now += 200;
      return now;
    });

    scanWithHandheld("C-1-SLOW");

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(asked.some((config) => config.url === "/api/v1/scans")).toBe(false);
  });

  it("types into the field that has focus, as it always has", async () => {
    const asked = await openSignedIn("/reports", signedIn(), ANSWERS);
    const search = await screen.findByRole("combobox", { name: "Search Hoard" });
    search.focus();

    for (const key of "C-1") fireEvent.keyDown(search, { key });

    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(asked.some((config) => config.url === "/api/v1/scans")).toBe(false);
  });
});
