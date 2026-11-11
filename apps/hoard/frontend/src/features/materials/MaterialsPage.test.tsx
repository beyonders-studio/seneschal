import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { material, SETTINGS } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

const list = (...items: ReturnType<typeof material>[]) => ({
  "GET /api/v1/materials": { status: 200, data: { items, total: items.length } },
});

describe("MaterialsPage", () => {
  it("lists materials with their stock, marking what is low, and no costs", async () => {
    await openSignedIn("/stock/materials", signedIn(), {
      ...SETTINGS,
      ...list(
        material({ on_hand: "2.5", low_stock_limit: "5.000000", average_cost: null }),
        material({ id: 6, name: "Sencha", on_hand: "40", category_id: null })
      ),
    });

    const oolong = (await screen.findByRole("link", { name: "Oolong" })).closest("tr");
    if (!oolong) throw new Error("no row");
    await within(oolong).findByText("Tea");
    within(oolong).getByText("Low stock");
    within(oolong).getByText("2.5 g");
    expect(screen.queryByText("Average cost")).toBeNull();
  });

  it("shows average costs to a member who sees them", async () => {
    await openSignedIn("/stock/materials", signedIn({ permissions: ["use", SEE_COSTS] }), {
      ...SETTINGS,
      ...list(material({ average_cost: "2.500000" })),
    });

    await screen.findByText("Average cost");
    await screen.findByText("$2.50");
  });

  it("drops what was typed in the add dialog when it closes", async () => {
    await openSignedIn("/stock/materials", signedIn(), { ...SETTINGS, ...list(material()) });

    fireEvent.click(await screen.findByRole("button", { name: "Add material" }));
    fireEvent.change(await screen.findByLabelText("Name"), { target: { value: "Matcha" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    fireEvent.click(await screen.findByRole("button", { name: "Add material" }));

    expect(await screen.findByLabelText("Name")).toHaveProperty("value", "");
  });

  it("asks a community with no materials to add its first", async () => {
    await openSignedIn("/stock/materials", signedIn(), { ...SETTINGS, ...list() });

    await screen.findByText("No materials yet");
    screen.getByRole("button", { name: "Add material" });
  });
});
