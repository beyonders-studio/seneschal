import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { SETTINGS } from "@/features/items/testing";
import { setCommunityWords } from "@/i18n";
import { type Answer, openSignedIn, signedIn } from "@/testing";

const NONE = { status: 200, data: { items: [], total: 0 } };

/** The settings of a community that names its own words. */
const named = (words: Record<string, string | null>): Record<string, Answer> => ({
  ...SETTINGS,
  "GET /api/v1/settings": {
    status: 200,
    data: { currency: "USD", auto_manufacture: "off", regimes: [], ...words },
  },
  "GET /api/v1/recipes": NONE,
  "GET /api/v1/bases": NONE,
  "GET /api/v1/materials": NONE,
});

const selectedTab = async (list: string) => {
  const tabs = await screen.findByRole("tablist", { name: list });
  return within(tabs).getByRole("tab", { selected: true }).textContent;
};

afterEach(() => setCommunityWords({ base: null, recipe: null }));

describe("A community's words", () => {
  it("say recipe and base until the community names its own", async () => {
    await openSignedIn(
      "/production/recipes",
      signedIn(),
      named({ base_word: null, recipe_word: null })
    );

    expect(await selectedTab("Production records")).toBe("Recipes");
    await screen.findByText("No recipes yet");
  });

  it("name every page's recipes and bases as typed", async () => {
    await openSignedIn(
      "/production/recipes",
      signedIn(),
      named({ base_word: "sub-assembly", recipe_word: "bill of materials" })
    );

    await waitFor(async () =>
      expect(await selectedTab("Production records")).toBe("Bill of materials")
    );
    await screen.findByText("No bill of materials yet");
    screen.getByText(/what goes into each product and sub-assembly/);
  });

  it("are named by an admin in Settings, and cleared back to Hoard's own", async () => {
    const asked = await openSignedIn(
      "/settings",
      signedIn({ page: "settings", admin: true }),
      named({ base_word: null, recipe_word: "formula" })
    );

    // Settings is held back while its sections load their words, so each step
    // finds the form afresh and waits for it to show.
    const recipe = () => screen.getByLabelText("Word for a recipe");
    const base = () => screen.getByLabelText("Word for a base");
    await waitFor(() => expect(recipe()).toHaveProperty("value", "formula"));
    expect(base()).toHaveProperty("placeholder", "base");
    fireEvent.change(base(), { target: { value: " blend " } });
    fireEvent.change(recipe(), { target: { value: "" } });
    await waitFor(() => expect(recipe()).toHaveProperty("value", ""));
    const form = recipe().closest("form") as HTMLElement;
    fireEvent.click(await within(form).findByRole("button", { name: "Save" }));
    await waitFor(() =>
      expect(
        asked.find((request) => request.method === "put" && request.url === "/api/v1/settings")
          ?.data
      ).toBe(JSON.stringify({ base_word: "blend", recipe_word: null }))
    );
  });
});
