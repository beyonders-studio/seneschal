import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ScanRead, Suggestions } from "@/api/generated/hoard.schemas";
import { buildHit, buildResults } from "@/features/search/testing";
import { openSignedIn, signedIn } from "@/testing";

const suggest = (answer: Suggestions) => ({
  "GET /api/v1/search/suggest": { status: 200, data: answer },
  "GET /api/v1/search": { status: 200, data: buildResults() },
});

const field = () => screen.findByRole("combobox", { name: "Search Hoard" });

/** What Hoard holds of a scan: nothing, unless told otherwise. */
const scanRead = (overrides: Partial<ScanRead> = {}): ScanRead => ({
  kind: "gs1",
  text: "",
  gtin: null,
  lot: null,
  serial: null,
  sscc: null,
  expires_on: null,
  best_before: null,
  produced_on: null,
  count: null,
  items: [],
  lots: [],
  problems: [],
  ...overrides,
});

const lotOf = (id: number) => ({
  id,
  item_id: 7,
  location_id: 1,
  number: "L24-118",
  expires_on: null,
  balance: "5",
  on_hold: false,
  loose: false,
  path: `/stock/lots/${id}`,
});

const sencha = {
  id: 7,
  kind: "material" as const,
  name: "Sencha",
  attributes: {},
  archived: false,
  path: "/stock/materials/7",
};

describe("SearchField", () => {
  it("is focused by Ctrl-K and Cmd-K", async () => {
    await openSignedIn("/reports", signedIn());
    const search = await field();

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(document.activeElement).toBe(search);
    search.blur();
    fireEvent.keyDown(window, { key: "K", metaKey: true });
    expect(document.activeElement).toBe(search);
  });

  it("suggests records as they are typed, grouped by kind, exact codes first", async () => {
    const asked = await openSignedIn(
      "/reports",
      signedIn(),
      suggest({
        items: [
          buildHit({ exact: true }),
          buildHit({ kind: "material", record_id: 6, title: "Oolong", subtitle: null }),
          buildHit({ record_id: 5, title: "LOT-10" }),
        ],
        exact_match: true,
      })
    );

    fireEvent.change(await field(), { target: { value: "lot-1" } });

    const list = await screen.findByRole("listbox", { name: "Suggestions" });
    const groups = within(list).getAllByRole("group");
    expect(groups.map((group) => group.getAttribute("aria-label"))).toEqual([
      "Exact code",
      "Material",
      "Lot",
    ]);
    within(groups[0]).getByRole("option", { name: /LOT-1/ });
    within(groups[2]).getByRole("option", { name: /LOT-10/ });
    const question = asked.find((config) => config.url === "/api/v1/search/suggest");
    expect(question?.params).toEqual({ q: "lot-1" });
  });

  it("opens the record whose code is scanned when exactly one has it", async () => {
    const asked = await openSignedIn(
      "/reports",
      signedIn(),
      suggest({ items: [buildHit({ exact: true }), buildHit({ record_id: 5 })], exact_match: true })
    );
    const search = await field();

    // Scanned: the code and Enter at once, before any suggestion was shown.
    fireEvent.change(search, { target: { value: "LOT-1" } });
    fireEvent.keyDown(search, { key: "Enter" });

    await waitFor(() => expect(asked.map((config) => config.url)).toContain("/api/v1/lots/4"));
    expect(await field()).toHaveProperty("value", "");
  });

  it("opens the one lot a scanned label names, though its run and hold share its number", async () => {
    const asked = await openSignedIn("/reports", signedIn(), {
      ...suggest({
        items: [buildHit({ exact: true }), buildHit({ exact: true })],
        exact_match: false,
      }),
      "POST /api/v1/scans": {
        status: 200,
        data: scanRead({ lot: "L24-118", items: [sencha], lots: [lotOf(4)] }),
      },
    });
    const search = await field();

    fireEvent.change(search, { target: { value: "(01)09506000134352(10)L24-118" } });
    fireEvent.keyDown(search, { key: "Enter" });

    await waitFor(() => expect(asked.map((config) => config.url)).toContain("/api/v1/lots/4"));
    const scanned = asked.find((config) => config.url === "/api/v1/scans");
    expect(JSON.parse(scanned?.data)).toEqual({ text: "(01)09506000134352(10)L24-118" });
  });

  it("opens the one item a scan names when it names no single lot", async () => {
    const asked = await openSignedIn("/reports", signedIn(), {
      ...suggest({ items: [], exact_match: false }),
      "POST /api/v1/scans": {
        status: 200,
        data: scanRead({ items: [sencha], lots: [lotOf(4), lotOf(5)] }),
      },
    });
    const search = await field();

    fireEvent.change(search, { target: { value: "09506000134352" } });
    fireEvent.keyDown(search, { key: "Enter" });

    await waitFor(() => expect(asked.map((config) => config.url)).toContain("/api/v1/materials/7"));
  });

  it("searches a label Hoard holds nothing of by its GTIN", async () => {
    const asked = await openSignedIn("/reports", signedIn(), {
      ...suggest({ items: [], exact_match: false }),
      "POST /api/v1/scans": {
        status: 200,
        data: scanRead({ gtin: "09506000134352", problems: ["unknown_gtin"] }),
      },
    });
    const search = await field();

    fireEvent.change(search, { target: { value: "(01)09506000134352(10)X" } });
    fireEvent.keyDown(search, { key: "Enter" });

    await screen.findByRole("heading", { name: "Results for “09506000134352”" });
    expect(asked.filter((config) => config.url === "/api/v1/search")[0]?.params).toMatchObject({
      q: "09506000134352",
    });
  });

  it("opens every result when the query is no single code", async () => {
    const asked = await openSignedIn(
      "/reports",
      signedIn(),
      suggest({ items: [buildHit({ exact: true }), buildHit({ exact: true })], exact_match: false })
    );
    const search = await field();

    fireEvent.change(search, { target: { value: "LOT-1" } });
    fireEvent.keyDown(search, { key: "Enter" });

    await screen.findByRole("heading", { name: "Results for “LOT-1”" });
    const question = asked.find((config) => config.url === "/api/v1/search");
    expect(question?.params).toMatchObject({ q: "LOT-1", page: 1 });
    expect(asked.map((config) => config.url)).not.toContain("/api/v1/lots/4");
  });

  it("opens the suggestion the arrow keys choose", async () => {
    const asked = await openSignedIn(
      "/reports",
      signedIn(),
      suggest({
        items: [
          buildHit({ kind: "material", record_id: 6, title: "Oolong", path: "/stock/materials/6" }),
          buildHit(),
        ],
        exact_match: false,
      })
    );
    const search = await field();
    fireEvent.change(search, { target: { value: "oolo" } });
    await screen.findByRole("listbox");

    fireEvent.keyDown(search, { key: "ArrowDown" });
    fireEvent.keyDown(search, { key: "ArrowDown" });
    expect(screen.getByRole("option", { selected: true })).toHaveProperty(
      "textContent",
      "LOT-1Oolong"
    );
    fireEvent.keyDown(search, { key: "Enter" });

    await waitFor(() => expect(asked.map((config) => config.url)).toContain("/api/v1/lots/4"));
    expect(asked.map((config) => config.url)).not.toContain("/api/v1/materials/6");
  });
});
