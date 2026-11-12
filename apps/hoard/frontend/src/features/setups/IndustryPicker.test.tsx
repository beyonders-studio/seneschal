import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SetupsRead } from "@/api/generated/hoard.schemas";
import { SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

const offered = (added: boolean, started = false): SetupsRead => ({
  setups: [
    { key: "bakery_food", added: false, words: ["units.dozen"] },
    { key: "cosmetics_soap", added: false, words: ["hold_reasons.curing"] },
    { key: "drinks", added, words: ["units.fl_oz", "reasons.spill"] },
    { key: "restaurant_bar", added: false, words: ["reasons.sent_back"] },
  ],
  started,
});

const answers = (setups: SetupsRead) => ({
  ...SETTINGS,
  "GET /api/v1/materials": { status: 200, data: { items: [], total: 0 } },
  "GET /api/v1/setups": { status: 200, data: setups },
  "POST /api/v1/setups/drinks/add": { status: 200, data: offered(true).setups[2] },
  "POST /api/v1/setups/blank": { status: 200, data: offered(false, true) },
});

// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const admin = signedIn({ admin: true });
const QUESTION = "What kind of business is this?";

async function open() {
  await screen.findByRole("heading", { name: QUESTION });
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Kind of business" }), { key: "Enter" });
  return screen.findAllByRole("option");
}

async function choose(industry: string) {
  await screen.findByRole("heading", { name: QUESTION });
  const start = screen.getByRole("button", { name: "Start" });
  expect(start).toHaveProperty("disabled", true);
  await open();
  fireEvent.click(screen.getByRole("option", { name: industry }));
  fireEvent.click(start);
}

describe("IndustryPicker", () => {
  it("lists each industry Hoard has a setup for, and something else", async () => {
    await openSignedIn("/stock/materials", admin, answers(offered(false)));

    const options = await open();

    expect(options.map((option) => option.textContent)).toEqual([
      "Bakery and food",
      "Cosmetics and soap",
      "Tea, coffee and drinks",
      "Restaurant, café and bar",
      "Something else",
    ]);
  });

  it("adds the industry's setup in the admin's words", async () => {
    const asked = await openSignedIn("/stock/materials", admin, answers(offered(false)));

    await choose("Tea, coffee and drinks");

    await waitFor(() =>
      expect(
        asked.find((request) => request.method === "post" && request.url?.includes("setups"))?.data
      ).toBe(JSON.stringify({ words: { "units.fl_oz": "fl oz", "reasons.spill": "Spill" } }))
    );
  });

  it("starts blank for something else", async () => {
    const asked = await openSignedIn("/stock/materials", admin, answers(offered(false)));

    await choose("Something else");

    await waitFor(() =>
      expect(
        asked.some((request) => request.method === "post" && request.url === "/api/v1/setups/blank")
      ).toBe(true)
    );
    expect(asked.some((request) => request.url?.endsWith("/add"))).toBe(false);
  });

  it("is not asked of a member", async () => {
    const asked = await openSignedIn("/stock/materials", signedIn(), answers(offered(false)));

    await screen.findByRole("heading", { level: 1 });
    expect(asked.some((request) => request.url === "/api/v1/setups")).toBe(false);
    expect(screen.queryByRole("heading", { name: QUESTION })).toBeNull();
  });

  it.each([
    ["once the organization has begun", offered(false, true)],
    ["once a setup is added", offered(true)],
  ])("is not asked %s", async (_when, setups) => {
    const asked = await openSignedIn("/stock/materials", admin, answers(setups));

    await waitFor(() =>
      expect(asked.some((request) => request.url === "/api/v1/setups")).toBe(true)
    );
    expect(screen.queryByRole("heading", { name: QUESTION })).toBeNull();
  });
});
