import { fireEvent, screen, waitFor, within } from "@testing-library/react";
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
  "GET /api/v1/properties": {
    status: 200,
    data: [
      {
        id: 8,
        kind: "material",
        name: "Grade",
        type: "select",
        options: [{ value: "first", label: "First", color: "#22C55E" }],
        currency: null,
        counter: null,
      },
    ],
  },
};

/** Opens the first row menu of `name` and chooses `action`. */
const choose = async (name: string, action: string) => {
  const [menu] = await screen.findAllByRole("button", { name: `Actions for ${name}` });
  fireEvent.keyDown(menu, { key: "Enter" });
  fireEvent.click(await screen.findByRole("menuitem", { name: action }));
};

const sent = (asked: { method?: string; url?: string; data?: string }[], method: string) =>
  asked.find(
    (request) => request.method === method && request.url?.startsWith("/api/v1/properties/")
  );

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
    expect(screen.queryByRole("heading", { name: "Properties" })).toBeNull();
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

  it("lets an admin add an option to a select", async () => {
    const asked = await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      ...SETTINGS,
      "PATCH /api/v1/properties/8": { status: 200, data: {} },
    });

    const properties = await screen.findByRole("region", { name: "Properties" });
    within(properties).getByRole("heading", { name: "Lots" });
    await choose("Grade", "Options");
    const dialog = await screen.findByRole("dialog", { name: "Options of Grade" });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add option" }));
    const [, added] = within(dialog).getAllByLabelText("Option label");
    fireEvent.change(added, { target: { value: "Second grade" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(JSON.parse(sent(asked, "patch")?.data ?? "")).toEqual({
      options: [
        { value: "first", label: "First", color: "#22C55E" },
        { value: "second_grade", label: "Second grade", color: null },
      ],
    });
  });

  it("asks an admin before deleting a property", async () => {
    const asked = await openSignedIn("/settings", signedIn({ page: "settings", admin: true }), {
      ...SETTINGS,
      "DELETE /api/v1/properties/8": { status: 204 },
    });

    await choose("Grade", "Delete");
    fireEvent.click(await screen.findByRole("button", { name: "Delete" }));

    await waitFor(() => expect(sent(asked, "delete")).toBeDefined());
  });
});
