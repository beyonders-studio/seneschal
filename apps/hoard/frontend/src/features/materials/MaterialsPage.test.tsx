import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { material, SETTINGS } from "@/features/items/testing";
import { SEE_COSTS } from "@/lib/permissions";
import { openSignedIn, signedIn } from "@/testing";

// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

/** The standard gram and kilogram, and a jar of the community's own. */
const UNITS = {
  "GET /api/v1/settings/units": {
    status: 200,
    data: [
      { id: 1, name: "g", standard: "gram", measure: "mass", aliases: ["g", "grams", "gramme"] },
      { id: 2, name: "kg", standard: "kilogram", measure: "mass", aliases: ["kg", "kilo"] },
      { id: 3, name: "Jar", standard: null, measure: null, aliases: [] },
    ],
  },
};

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

  it("shows archived materials only when asked", async () => {
    const asked = await openSignedIn("/stock/materials", signedIn(), {
      ...SETTINGS,
      ...list(material({ status: "archived" })),
    });

    fireEvent.click(await screen.findByRole("switch", { name: "Show archived" }));

    const oolong = (await screen.findByRole("link", { name: "Oolong" })).closest("tr");
    if (!oolong) throw new Error("no row");
    within(oolong).getByText("Archived");
    const listed = asked.filter((request) => request.url === "/api/v1/materials");
    expect(listed.map((request) => request.params)).toEqual([
      { offset: 0, limit: 50 },
      { offset: 0, limit: 50, archived: true },
    ]);
  });

  it("adds a material with its notes, bin and starting stock at the main location", async () => {
    const asked = await openSignedIn(
      "/stock/materials",
      signedIn({ permissions: ["use", SEE_COSTS] }),
      {
        ...SETTINGS,
        ...list(),
        "POST /api/v1/materials": { status: 201, data: { ...material(), stock: [], lots: [] } },
        "GET /api/v1/materials/5": { status: 200, data: { ...material(), stock: [], lots: [] } },
      }
    );
    fireEvent.click(await screen.findByRole("button", { name: "Add material" }));
    const dialog = await screen.findByRole("dialog", { name: "Add material" });

    fireEvent.change(within(dialog).getByLabelText("Name"), { target: { value: "Oolong" } });
    fireEvent.click(within(dialog).getByRole("combobox", { name: "Counted in" }));
    fireEvent.click(await screen.findByRole("option", { name: /^g/ }));
    fireEvent.change(within(dialog).getByLabelText("Notes"), { target: { value: "Keep sealed" } });
    fireEvent.change(within(dialog).getByLabelText("Starting stock, in g"), {
      target: { value: "500" },
    });
    fireEvent.change(within(dialog).getByLabelText("Unit cost"), { target: { value: "0.04" } });
    fireEvent.change(within(dialog).getByLabelText("Zone"), { target: { value: " Shelf A " } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(
        JSON.parse(
          String(
            asked.find(
              (request) => request.url === "/api/v1/materials" && request.method === "post"
            )?.data
          )
        )
      ).toMatchObject({
        name: "Oolong",
        notes: "Keep sealed",
        start: {
          location_id: null,
          quantity: "500",
          unit_cost: "0.04",
          bin: { zone: "Shelf A", section: null, position: null },
        },
      })
    );
  });

  it("finds a unit by its aliases, and offers only an admin a new one", async () => {
    await openSignedIn("/stock/materials", signedIn(), { ...SETTINGS, ...UNITS, ...list() });
    fireEvent.click(await screen.findByRole("button", { name: "Add material" }));
    const dialog = await screen.findByRole("dialog", { name: "Add material" });

    fireEvent.click(await within(dialog).findByRole("combobox", { name: "Counted in" }));
    fireEvent.change(await screen.findByPlaceholderText("Search units"), {
      target: { value: "gramme" },
    });

    await screen.findByRole("option", { name: /gram/ });
    expect(screen.queryByRole("option", { name: /kilogram/ })).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("Search units"), { target: { value: "bag" } });
    await screen.findByText("No unit matches.");
    expect(screen.queryByRole("option", { name: /as a new unit/ })).toBeNull();
  });

  it("lets an admin add a unit there, and fills in a standard conversion", async () => {
    const asked = await openSignedIn("/stock/materials", signedIn({ admin: true }), {
      ...SETTINGS,
      ...UNITS,
      ...list(),
      "POST /api/v1/settings/units": {
        status: 201,
        data: { id: 4, name: "Bag", standard: null, measure: null, aliases: [] },
      },
      "GET /api/v1/settings/units/conversion": { status: 200, data: { factor: "1000.000000" } },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Add material" }));
    const dialog = await screen.findByRole("dialog", { name: "Add material" });

    fireEvent.click(await within(dialog).findByRole("combobox", { name: "Counted in" }));
    fireEvent.change(await screen.findByPlaceholderText("Search units"), {
      target: { value: "kilo" },
    });
    expect(screen.queryByRole("option", { name: /as a new unit/ })).toBeNull();
    fireEvent.change(screen.getByPlaceholderText("Search units"), { target: { value: "Bag" } });
    fireEvent.click(await screen.findByRole("option", { name: "Use “Bag” as a new unit" }));
    await waitFor(() =>
      expect(
        asked.find(
          (request) => request.url === "/api/v1/settings/units" && request.method === "post"
        )?.data
      ).toBe(JSON.stringify({ name: "Bag" }))
    );

    fireEvent.click(within(dialog).getByRole("combobox", { name: "Bought in" }));
    fireEvent.click(await screen.findByRole("option", { name: /kilogram/ }));
    const conversion = await within(dialog).findByDisplayValue("1000.000000");
    expect(conversion).toHaveProperty("disabled", true);
    within(dialog).getByText("Set by the two units.");
  });
});
