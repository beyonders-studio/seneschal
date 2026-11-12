import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SETTINGS } from "@/features/items/testing";
import { lotRead } from "@/features/lots/testing";
import { type Answer, openSignedIn } from "@/testing";

// The dialogs' checkboxes measure themselves, which jsdom has nothing to do with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};

// The kit's options scroll themselves into view, which jsdom cannot.
Element.prototype.scrollIntoView ??= () => {};

const HOLD = {
  id: 6,
  reason_id: 2,
  ready_on: "2026-12-01",
  notes: "Smells off",
  run_id: null,
  order_id: 5,
  from_hold_id: null,
  created_at: "2026-11-02T10:00:00Z",
  created_by: "member-ref",
};

const ANSWERS: Record<string, Answer> = {
  ...SETTINGS,
  "GET /api/v1/holds/reasons": {
    status: 200,
    data: [
      { id: 1, key: "curing", name: null },
      { id: 2, key: "suspect", name: null },
      { id: 3, key: null, name: "Mould" },
    ],
  },
  "GET /api/v1/settings/reasons": {
    status: 200,
    data: [{ id: 8, key: "expired", name: null }],
  },
};

const posted = (asked: { method?: string; url?: string; data?: string }[], url: string) =>
  JSON.parse(asked.find((request) => request.method === "post" && request.url === url)?.data ?? "");

/** Opens the choice labelled `name` in `dialog` and picks `option`. */
async function choose(dialog: HTMLElement, name: string, option: string) {
  fireEvent.keyDown(within(dialog).getByRole("combobox", { name }), { key: "Enter" });
  fireEvent.click(await screen.findByRole("option", { name: option }));
}

describe("HoldSection", () => {
  it("puts an available lot on hold with what was made from it", async () => {
    const asked = await openSignedIn("/stock/lots/3", undefined, {
      ...ANSWERS,
      "GET /api/v1/lots/3": { status: 200, data: lotRead() },
      "POST /api/v1/holds": { status: 201, data: [{ id: 6 }, { id: 7 }] },
    });

    await screen.findByText("Available.");
    fireEvent.click(screen.getByRole("button", { name: "Put on hold" }));
    const dialog = await screen.findByRole("dialog", { name: "Put L-3 on hold" });
    await choose(dialog, "Reason", "Mould");
    fireEvent.change(within(dialog).getByLabelText("Ready on"), {
      target: { value: "2026-12-01" },
    });
    fireEvent.click(within(dialog).getByLabelText("Also hold everything made from it"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Put on hold" }));

    await screen.findByText("2 lots put on hold.");
    expect(posted(asked, "/api/v1/holds")).toEqual({
      lot_id: 3,
      reason_id: 3,
      ready_on: "2026-12-01",
      notes: null,
      made_from: true,
    });
  });

  it("shows a held lot's hold, and discards some of it", async () => {
    const asked = await openSignedIn("/stock/lots/3", undefined, {
      ...ANSWERS,
      "GET /api/v1/lots/3": { status: 200, data: lotRead({ on_hold: true, hold: HOLD }) },
      "POST /api/v1/holds/6/discard": { status: 200, data: { id: 6 } },
    });

    await screen.findByText("On hold: Suspect.");
    screen.getByText("Smells off");
    expect(screen.getByRole("link", { name: "Placed from an order." })).toHaveProperty(
      "href",
      expect.stringContaining("/orders/5")
    );
    fireEvent.click(screen.getByRole("button", { name: "Discard" }));
    const dialog = await screen.findByRole("dialog", { name: "Discard from L-3" });
    await choose(dialog, "Reason", "Expired");
    fireEvent.change(within(dialog).getByLabelText("Quantity"), { target: { value: "4" } });
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2026-12-02" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Discard" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(posted(asked, "/api/v1/holds/6/discard")).toEqual({
      reason_id: 8,
      quantity: "4",
      ended_on: "2026-12-02",
    });
  });

  it("says why a hold could not be released", async () => {
    await openSignedIn("/stock/lots/3", undefined, {
      ...ANSWERS,
      "GET /api/v1/lots/3": { status: 200, data: lotRead({ on_hold: true, hold: HOLD }) },
      "POST /api/v1/holds/6/release": { status: 409, data: { detail: "HOLD_NOT_READY" } },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Release" }));
    const dialog = await screen.findByRole("dialog", { name: "Release L-3" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Release" }));

    await screen.findByText("It isn't ready yet: release it on or after its ready date.");
  });
});
