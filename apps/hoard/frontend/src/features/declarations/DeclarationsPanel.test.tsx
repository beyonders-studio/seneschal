import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { DeclarationRead } from "@/api/generated/hoard.schemas";
import { materialDetail, SETTINGS } from "@/features/items/testing";
import { openSignedIn, signedIn } from "@/testing";

const declared = (overrides: Partial<DeclarationRead> = {}): DeclarationRead => ({
  records_own: true,
  recorded_allergens: null,
  ingredient_name: null,
  ingredients: ["Oolong"],
  allergens: [],
  undeclared: ["Oolong"],
  recorded_fragrance: null,
  fragrance_recorded: false,
  rinse_off: false,
  fragrance_threshold: "0.001",
  fragrance_shares: {},
  fragrance_allergens: [],
  fragrance_undeclared: ["Oolong"],
  ...overrides,
});

// The fragrance picker measures itself, which jsdom has nothing to do with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

const put = (asked: { method?: string; url?: string; data?: unknown }[]) =>
  asked.find((request) => request.method === "put" && request.url?.includes("declarations"))?.data;

const opened = (declaration: DeclarationRead) => ({
  ...SETTINGS,
  "GET /api/v1/materials/5": { status: 200, data: materialDetail() },
  "GET /api/v1/declarations/material/5": { status: 200, data: declaration },
  "PUT /api/v1/declarations/material/5": {
    status: 200,
    data: declared({ recorded_allergens: ["milk"], allergens: ["milk"], undeclared: [] }),
  },
});

describe("DeclarationsPanel", () => {
  it("says what a material contains, and what is not recorded", async () => {
    await openSignedIn(
      "/stock/materials/5",
      signedIn(),
      opened(declared({ allergens: ["milk", "tree_nuts"], undeclared: ["Oolong"] }))
    );

    await screen.findByText("Milk");
    screen.getByText("Tree nuts");
    screen.getByText("Allergens aren't recorded for Oolong.");
    expect(screen.queryByText("Fragrance allergens to declare")).toBeNull();
  });

  it("says which fragrance allergens are past the threshold", async () => {
    await openSignedIn(
      "/stock/materials/5",
      signedIn(),
      opened(
        declared({
          recorded_fragrance: { linalool: "0.5", limonene: "0.0005" },
          fragrance_recorded: true,
          fragrance_shares: { linalool: "0.5", limonene: "0.0005" },
          fragrance_allergens: ["linalool"],
          fragrance_undeclared: ["Rose water"],
        })
      )
    );

    await screen.findByText("Linalool 0.5%");
    expect(screen.queryByText(/Limonene/)).toBeNull();
    screen.getByText("Declared past 0.001%, the threshold for a product left on the skin.");
    screen.getByText("Fragrance allergens aren't recorded for Rose water.");
  });

  it("records a material's allergens", async () => {
    const asked = await openSignedIn("/stock/materials/5", signedIn(), opened(declared()));

    fireEvent.click(await screen.findByRole("button", { name: "Edit allergens" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.change(within(dialog).getByLabelText("Name on ingredient lists"), {
      target: { value: "Oolong tea" },
    });
    fireEvent.click(within(dialog).getByLabelText("Its supplier declares its allergens"));
    fireEvent.click(within(dialog).getByLabelText("Milk"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(put(asked)).toBe(
        JSON.stringify({
          ingredient_name: "Oolong tea",
          allergens: ["milk"],
          fragrance_allergens: null,
        })
      )
    );
  });

  it("records a material's fragrance allergens", async () => {
    const asked = await openSignedIn("/stock/materials/5", signedIn(), opened(declared()));

    fireEvent.click(await screen.findByRole("button", { name: "Edit allergens" }));
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(within(dialog).getByLabelText("Its supplier declares its fragrance allergens"));
    fireEvent.click(
      within(dialog).getByRole("combobox", { name: "Fragrance allergens it contains" })
    );
    fireEvent.click(await screen.findByRole("option", { name: "Linalool" }));
    fireEvent.change(await within(dialog).findByLabelText("Linalool, % of it"), {
      target: { value: "2" },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(put(asked)).toBe(
        JSON.stringify({
          ingredient_name: null,
          allergens: null,
          fragrance_allergens: { linalool: "2" },
        })
      )
    );
  });

  it("leaves out recording while Hoard is read-only", async () => {
    await openSignedIn("/stock/materials/5", signedIn({ read_only: true }), opened(declared()));

    await screen.findByText("Allergens aren't recorded for Oolong.");
    expect(screen.queryByRole("button", { name: "Edit allergens" })).toBeNull();
  });
});
