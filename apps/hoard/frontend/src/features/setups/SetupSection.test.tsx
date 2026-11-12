import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { SetupsRead } from "@/api/generated/hoard.schemas";
import { SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

const offered = (added: boolean, started = false): SetupsRead => ({
  setups: [{ key: "drinks", added, words: ["units.fl_oz", "reasons.spill"] }],
  started,
});

const answers = (setups: SetupsRead) => ({
  ...SETTINGS,
  "GET /api/v1/materials": { status: 200, data: { items: [], total: 0 } },
  "GET /api/v1/setups": { status: 200, data: setups },
  "POST /api/v1/setups/drinks/add": { status: 200, data: offered(true).setups[0] },
});

describe("SetupSection", () => {
  it("lists each setup and whether it is added", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      ...answers(offered(true)),
      "GET /api/v1/settings/locations": { status: 200, data: [] },
      "GET /api/v1/settings/reasons": { status: 200, data: [] },
      "GET /api/v1/properties": { status: 200, data: [] },
    });

    const section = (await screen.findByRole("heading", { name: "Starting setups" })).closest(
      "section"
    );
    if (!section) throw new Error("no section");
    await within(section).findByText("Drinks");
    within(section).getByText("Added");
    expect(within(section).queryByRole("button", { name: "Add" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "Start from a setup?" })).toBeNull();
  });
});
