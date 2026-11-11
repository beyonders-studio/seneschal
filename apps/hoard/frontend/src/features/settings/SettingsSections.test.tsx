import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { type Answer, openSignedIn, signedIn } from "@/testing";

/** What Settings reads: one of each kind of setting. */
const SETTINGS: Record<string, Answer> = {
  "GET /api/v1/settings": { status: 200, data: { currency: "EUR" } },
  "GET /api/v1/settings/units": { status: 200, data: [{ id: 1, name: "kg" }] },
  "GET /api/v1/settings/categories": {
    status: 200,
    data: [{ id: 2, kind: "material", name: "Tea" }],
  },
  "GET /api/v1/settings/locations": {
    status: 200,
    data: [
      { id: 3, name: null, main: true },
      { id: 4, name: "Back room", main: false },
    ],
  },
  "GET /api/v1/settings/reasons": {
    status: 200,
    data: [
      { id: 5, key: null, name: "Spilled" },
      { id: 7, key: "personal_use", name: null },
    ],
  },
};

describe("Settings", () => {
  it("shows a member the settings without a way to change them", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings" }), SETTINGS);

    await screen.findByText("kg");
    screen.getByText("Main location");
    screen.getByText("Back room");
    screen.getByText("Tea");
    screen.getByText("Spilled");
    screen.getByText("Personal use");
    screen.getByText("Euro (EUR)");
    expect(screen.queryByRole("button", { name: "Add unit" })).toBeNull();
    expect(screen.queryByLabelText("Currency code")).toBeNull();
    expect(screen.queryByRole("button", { name: /kg/ })).toBeNull();
  });

  it("lets an admin add to them", async () => {
    const asked = await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      ...SETTINGS,
      "POST /api/v1/settings/units": { status: 201, data: { id: 6, name: "box" } },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Add unit" }));
    fireEvent.change(await screen.findByLabelText("Unit"), { target: { value: " box " } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByLabelText("Unit")).toBeNull());
    const added = asked.find(
      (request) => request.url === "/api/v1/settings/units" && request.method === "post"
    );
    expect(JSON.parse(added?.data)).toEqual({ name: "box" });
    expect(screen.getByLabelText("Currency code")).toHaveProperty("value", "EUR");
  });

  it("keeps the dialog open with the reason a name was refused", async () => {
    await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      ...SETTINGS,
      "POST /api/v1/settings/units": { status: 409, data: { detail: "NAME_TAKEN" } },
    });

    fireEvent.click(await screen.findByRole("button", { name: "Add unit" }));
    fireEvent.change(await screen.findByLabelText("Unit"), { target: { value: "kg" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("That name is already used.");
  });
});
