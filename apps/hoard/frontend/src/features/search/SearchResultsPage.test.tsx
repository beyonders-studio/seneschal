import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SearchResults } from "@/api/generated/hoard.schemas";
import { buildHit, buildResults } from "@/features/search/testing";
import { openSignedIn, signedIn } from "@/testing";

// The kit's switch measures itself, which jsdom cannot.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

const answering = (results: SearchResults) => ({
  "GET /api/v1/search": { status: 200, data: results },
});

const searches = (asked: { url?: string; params?: unknown }[]) =>
  asked.filter((config) => config.url === "/api/v1/search").map((config) => config.params);

describe("SearchResultsPage", () => {
  it("shows each kind's count, and results with their matched words marked", async () => {
    const asked = await openSignedIn(
      "/search?q=oolong",
      signedIn(),
      answering(
        buildResults({
          items: [
            buildHit({
              snippet: [
                { text: "Oolong", marked: true },
                { text: " from <b>Fujian</b>", marked: false },
              ],
            }),
          ],
          total: 3,
          counts: { lot: 1, material: 2 },
        })
      )
    );

    await screen.findByRole("heading", { name: "Results for “oolong”" });
    const result = (await screen.findByRole("link", { name: "LOT-1" })).closest("li");
    if (!result) throw new Error("no result");
    const tabs = screen.getByRole("tablist", { name: "Kinds of record" });
    expect(
      within(tabs)
        .getAllByRole("tab")
        .map((tab) => tab.textContent)
    ).toEqual(["All3", "Materials2", "Lots1"]);
    expect(within(result).getByText("Oolong", { selector: "mark" })).toBeTruthy();
    within(result).getByText("from <b>Fujian</b>", { exact: false });
    screen.getByText(/Put words in quotes/);
    expect(searches(asked)).toEqual([{ q: "oolong", page: 1 }]);
  });

  it("asks for one kind, and for archived records, when told to", async () => {
    const asked = await openSignedIn(
      "/search?q=oolong",
      signedIn(),
      answering(buildResults({ items: [buildHit()], total: 1, counts: { lot: 1 } }))
    );

    const lots = await screen.findByRole("tab", { name: "Lots1" });
    fireEvent.mouseDown(lots);
    fireEvent.click(lots);
    await waitFor(() =>
      expect(searches(asked)).toContainEqual({ q: "oolong", kind: "lot", page: 1 })
    );
    fireEvent.click(screen.getByRole("switch", { name: "Include archived" }));
    await waitFor(() =>
      expect(searches(asked)).toContainEqual({ q: "oolong", kind: "lot", archived: true, page: 1 })
    );
  });

  it("offers close matches when nothing matched", async () => {
    await openSignedIn(
      "/search?q=olong",
      signedIn(),
      answering(buildResults({ close_matches: [buildHit({ title: "Oolong", kind: "material" })] }))
    );

    await screen.findByText("Nothing matches “olong”");
    const offered = screen.getByRole("region", { name: "Did you mean…" });
    expect(within(offered).getByRole("link", { name: "Oolong" })).toHaveProperty(
      "pathname",
      "/stock/lots/4"
    );
  });
});
