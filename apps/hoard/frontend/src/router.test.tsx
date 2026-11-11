import { screen } from "@testing-library/react";
import { describe, it } from "vitest";

import { openSignedIn } from "@/testing";

describe("Hoard's pages", () => {
  it.each([
    ["/stock", "Stock", "No stock yet"],
    ["/production", "Production", "No production runs yet"],
    ["/reports", "Reports", "No reports yet"],
    ["/settings", "Settings", "Nothing to set up yet"],
  ])("%s opens its page", async (path, title, empty) => {
    await openSignedIn(path);
    await screen.findByRole("heading", { name: title });
    screen.getByText(empty);
  });
});
