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
  ...overrides,
});

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
      expect(
        asked.find((request) => request.method === "put" && request.url?.includes("declarations"))
          ?.data
      ).toBe(JSON.stringify({ ingredient_name: "Oolong tea", allergens: ["milk"] }))
    );
  });

  it("leaves out recording while Hoard is read-only", async () => {
    await openSignedIn("/stock/materials/5", signedIn({ read_only: true }), opened(declared()));

    await screen.findByText("Allergens aren't recorded for Oolong.");
    expect(screen.queryByRole("button", { name: "Edit allergens" })).toBeNull();
  });
});
