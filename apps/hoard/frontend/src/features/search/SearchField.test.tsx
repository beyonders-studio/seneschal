import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Suggestions } from "@/api/generated/hoard.schemas";
import { buildHit, buildResults } from "@/features/search/testing";
import { openSignedIn, signedIn } from "@/testing";

const suggest = (answer: Suggestions) => ({
  "GET /api/v1/search/suggest": { status: 200, data: answer },
  "GET /api/v1/search": { status: 200, data: buildResults() },
});

const field = () => screen.findByRole("combobox", { name: "Search Hoard" });

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
