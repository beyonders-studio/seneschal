import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

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

const admin = signedIn({ admin: true });

describe("SetupSuggestion", () => {
  beforeEach(() => window.localStorage.clear());

  it("adds a setup in the admin's words", async () => {
    const asked = await openSignedIn("/stock/materials", admin, answers(offered(false)));

    await screen.findByRole("heading", { name: "Start from a setup?" });
    fireEvent.click(screen.getByRole("button", { name: /Drinks/ }));

    await waitFor(() =>
      expect(
        asked.find((request) => request.method === "post" && request.url?.includes("setups"))?.data
      ).toBe(JSON.stringify({ words: { "units.fl_oz": "fl oz", "reasons.spill": "Spill" } }))
    );
  });

  it("puts itself away for good when an admin starts blank", async () => {
    await openSignedIn("/stock/materials", admin, answers(offered(false)));

    fireEvent.click(await screen.findByRole("button", { name: "Start blank" }));

    expect(screen.queryByRole("heading", { name: "Start from a setup?" })).toBeNull();
    expect(window.localStorage.getItem("hoard.setupSuggestion.dismissed.community-ref")).toBe("1");
  });

  it("is not suggested to a member", async () => {
    const asked = await openSignedIn("/stock/materials", signedIn(), answers(offered(false)));

    await screen.findByRole("heading", { level: 1 });
    expect(asked.some((request) => request.url === "/api/v1/setups")).toBe(false);
  });

  it("is not suggested once the organization has begun", async () => {
    const asked = await openSignedIn("/stock/materials", admin, answers(offered(false, true)));

    await waitFor(() =>
      expect(asked.some((request) => request.url === "/api/v1/setups")).toBe(true)
    );
    expect(screen.queryByText("Start blank")).toBeNull();
  });
});
