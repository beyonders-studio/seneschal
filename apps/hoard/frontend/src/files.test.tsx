import { bindApiClient } from "@seneschal/app/auth";
import { FileImage, FileLink } from "@seneschal/app/files";
import { isNative } from "@seneschal/app/native/platform";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { apiClient } from "@/api/mutator";
import { answerApi } from "@/testing";

vi.mock("@seneschal/app/native/platform", () => ({ isNative: vi.fn(() => false) }));

const phone = vi.hoisted(() => ({
  written: [] as { path: string; data: string; directory: string }[],
  shared: [] as { files?: string[]; title?: string }[],
}));
vi.mock("@capacitor/filesystem", () => ({
  Directory: { Cache: "CACHE" },
  Filesystem: {
    writeFile: async (file: { path: string; data: string; directory: string }) => {
      phone.written.push(file);
      return { uri: `file:///cache/${file.path}` };
    },
  },
}));
vi.mock("@capacitor/share", () => ({
  Share: {
    share: async (what: { files?: string[]; title?: string }) => {
      phone.shared.push(what);
    },
  },
}));

const PICTURE = "/api/v1/items/7/picture/3";
const EXPORT = "/api/v1/jobs/4/result";

describe("the app's own files", () => {
  beforeEach(() => {
    bindApiClient(apiClient);
    phone.written.length = 0;
    phone.shared.length = 0;
  });

  it("are loaded by their path in a browser, where the cookie goes with them", () => {
    render(
      <>
        <FileImage path={PICTURE} alt="The picture" />
        <FileLink path={EXPORT} fileName="stock.csv">
          Download
        </FileLink>
      </>
    );

    expect(screen.getByRole("img", { name: "The picture" }).getAttribute("src")).toBe(PICTURE);
    const link = screen.getByRole("link", { name: "Download" });
    expect([link.getAttribute("href"), link.getAttribute("download")]).toEqual([
      EXPORT,
      "stock.csv",
    ]);
  });

  it("are fetched with the session on the phone, and shown from memory", async () => {
    vi.mocked(isNative).mockReturnValue(true);
    const made = vi.fn(() => "blob:picture");
    vi.stubGlobal("URL", Object.assign(URL, { createObjectURL: made, revokeObjectURL: vi.fn() }));
    const asked = answerApi({ [`GET ${PICTURE}`]: { status: 200, data: new Blob(["png"]) } });

    render(<FileImage path={PICTURE} alt="The picture" />);

    await waitFor(() =>
      expect(screen.getByRole("img", { name: "The picture" }).getAttribute("src")).toBe(
        "blob:picture"
      )
    );
    expect(asked[0].responseType).toBe("blob");
  });

  it("are saved and handed to the phone's share sheet when downloaded on the phone", async () => {
    vi.mocked(isNative).mockReturnValue(true);
    answerApi({ [`GET ${EXPORT}`]: { status: 200, data: new Blob(["a,b"]) } });

    render(
      <FileLink path={EXPORT} fileName="stock/2026.csv">
        Download
      </FileLink>
    );
    fireEvent.click(screen.getByRole("link", { name: "Download" }));

    await waitFor(() => expect(phone.shared).toHaveLength(1));
    expect(phone.written[0]).toMatchObject({ path: "stock_2026.csv", directory: "CACHE" });
    expect(atob(phone.written[0].data)).toBe("a,b");
    expect(phone.shared[0]).toEqual({
      files: ["file:///cache/stock_2026.csv"],
      title: "stock/2026.csv",
    });
  });

  it("say when the phone couldn't fetch a download", async () => {
    vi.mocked(isNative).mockReturnValue(true);
    answerApi({ [`GET ${EXPORT}`]: { status: 404 } });
    const failed = vi.fn();

    render(
      <FileLink path={EXPORT} onFailed={failed}>
        Download
      </FileLink>
    );
    fireEvent.click(screen.getByRole("link", { name: "Download" }));

    await waitFor(() => expect(failed).toHaveBeenCalled());
    expect(phone.shared).toHaveLength(0);
  });
});
