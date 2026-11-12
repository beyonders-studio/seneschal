import { fireEvent, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { type Answer, openSignedIn } from "@/testing";

const NOTICE = /Your community has no contacts yet/;

// The dialog's checkbox measures itself, which jsdom has nothing to do with.
globalThis.ResizeObserver ??= class {
  observe() {}
  unobserve() {}
  disconnect() {}
};
// The list scrolls the option it marks into view, which jsdom can't.
Element.prototype.scrollIntoView ??= () => {};

/** Opens the purchases list with no purchases, and the new purchase dialog on
 *  it, whose supplier picker reads the community's contacts from `contacts`. */
async function openNewPurchase(contacts: Answer, added: Answer = { status: 404 }) {
  await openSignedIn("/stock/purchases", undefined, {
    "GET /api/v1/purchases": { status: 200, data: { items: [], total: 0 } },
    "GET /api/v1/contacts": contacts,
    "POST /api/v1/contacts": added,
  });
  fireEvent.click(await screen.findByRole("button", { name: "New purchase" }));
  return screen.findByRole("dialog", { name: "New purchase" });
}

describe("SupplierPicker", () => {
  it("says when the community has no contacts to pick from", async () => {
    const dialog = await openNewPurchase({ status: 200, data: [] });
    await within(dialog).findByText(NOTICE);
  });

  it("says nothing of it when there are contacts", async () => {
    const dialog = await openNewPurchase({
      status: 200,
      data: [{ id: 4, name: "Mill & Co", is_organization: true }],
    });
    await within(dialog).findByText("Choose a supplier");
    expect(within(dialog).queryByText(NOTICE)).toBeNull();
  });

  it("says when Initiative can't be reached", async () => {
    const dialog = await openNewPurchase({
      status: 503,
      data: { detail: "INITIATIVE_UNAVAILABLE" },
    });
    await within(dialog).findByText(/Initiative can't be reached right now/);
  });

  it("clears the supplier picked", async () => {
    const dialog = await openNewPurchase({
      status: 200,
      data: [{ id: 4, name: "Mill & Co", is_organization: true }],
    });
    const picker = await within(dialog).findByRole("combobox", { name: "Supplier" });

    fireEvent.click(picker);
    expect(screen.queryByRole("option", { name: "Clear" })).toBeNull();
    fireEvent.click(await screen.findByRole("option", { name: "Mill & Co" }));
    await within(picker).findByText("Mill & Co");

    fireEvent.click(picker);
    fireEvent.click(await screen.findByRole("option", { name: "Clear" }));
    await within(picker).findByText("Choose a supplier");
  });

  it("says when Community contacts isn't installed to add a supplier to", async () => {
    const dialog = await openNewPurchase(
      { status: 200, data: [] },
      { status: 403, data: { detail: "COMMUNITY_CONTACTS_NOT_INSTALLED" } }
    );
    fireEvent.click(await within(dialog).findByRole("combobox", { name: "Supplier" }));
    fireEvent.change(await screen.findByPlaceholderText("Search contacts"), {
      target: { value: "Beans Ltd" },
    });
    fireEvent.click(await screen.findByRole("option", { name: "Add “Beans Ltd” as a contact" }));

    await screen.findByText(/the Community contacts plug-in, which isn't installed/);
  });
});
