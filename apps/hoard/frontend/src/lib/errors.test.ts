import { AxiosError, type AxiosResponse, type InternalAxiosRequestConfig } from "axios";
import { describe, expect, it } from "vitest";

import i18n from "@/i18n";
import { errorMessage } from "@/lib/errors";

const refused = (detail: unknown) => {
  const config = { headers: {} } as InternalAxiosRequestConfig;
  const response = { status: 403, data: { detail }, config } as AxiosResponse;
  return new AxiosError("refused", undefined, config, null, response);
};

describe("errorMessage", () => {
  it("reads the API's code in the page's words, then Hoard's, then the fallback", async () => {
    await i18n.changeLanguage("en");
    await i18n.loadNamespaces(["hoard", "connection"]);
    i18n.addResource("en", "connection", "errors.CONTACT_GONE", "That contact is gone.");
    const t = i18n.getFixedT("en", "connection");

    expect(errorMessage(refused("CONTACT_GONE"), t, "hoard:common.save")).toBe(
      "That contact is gone."
    );
    expect(errorMessage(refused("ADMIN_ONLY"), t, "hoard:common.save")).toBe(
      "Only the community's admins can do that."
    );
    expect(errorMessage(refused("SOMETHING_NEW"), t, "hoard:common.save")).toBe("Save");
    expect(errorMessage(new Error("offline"), t, "hoard:common.save")).toBe("Save");
  });
});
