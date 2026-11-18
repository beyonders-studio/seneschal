import "@/i18n";

import { isNative } from "@seneschal/app/native/platform";
import { ScanButton } from "@seneschal/app/scan";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { host } from "@/ui/host";

vi.mock("@seneschal/app/native/platform", () => ({ isNative: vi.fn(() => true) }));

const scanner = vi.hoisted(() => ({
  available: true,
  installed: 0,
  answer: (): Promise<{ barcodes: { rawValue?: string }[] }> => Promise.resolve({ barcodes: [] }),
  formats: [] as string[],
}));

vi.mock("@capacitor-mlkit/barcode-scanning", () => ({
  BarcodeFormat: new Proxy({}, { get: (_target, name) => String(name) }),
  BarcodeScanner: {
    isGoogleBarcodeScannerModuleAvailable: async () => ({ available: scanner.available }),
    installGoogleBarcodeScannerModule: async () => {
      scanner.installed += 1;
    },
    scan: (options: { formats: string[] }) => {
      scanner.formats = options.formats;
      return scanner.answer();
    },
  },
}));

const LABEL = "]d20109506000134352\u001d10L24-118";

describe("Scanning with the phone's camera", () => {
  beforeEach(() => {
    vi.mocked(isNative).mockReturnValue(true);
    scanner.available = true;
    scanner.installed = 0;
  });

  it("hands the scanned label's raw text to the page", async () => {
    scanner.answer = async () => ({ barcodes: [{ rawValue: LABEL }] });
    const scanned = vi.fn();
    render(<ScanButton onScan={scanned} />);

    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    await waitFor(() => expect(scanned).toHaveBeenCalledWith(LABEL));
    expect(scanner.formats).toContain("DataMatrix");
  });

  it("does nothing when the person closes the scanner", async () => {
    scanner.answer = () => Promise.reject(new Error("scan canceled."));
    const scanned = vi.fn();
    const said = vi.spyOn(host.toast, "error");
    render(<ScanButton onScan={scanned} />);

    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Scan" })).toHaveProperty("disabled", false)
    );
    expect(scanned).not.toHaveBeenCalled();
    expect(said).not.toHaveBeenCalled();
  });

  it("asks for Google's scanner module once, and says to try again", async () => {
    scanner.available = false;
    const said = vi.spyOn(host.toast, "error");
    render(<ScanButton onScan={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "Scan" }));

    await waitFor(() =>
      expect(said).toHaveBeenCalledWith(
        "The phone is getting its barcode scanner ready. Try again in a moment."
      )
    );
    expect(scanner.installed).toBe(1);
  });

  it("isn't drawn where the camera can't scan", () => {
    vi.mocked(isNative).mockReturnValue(false);

    render(<ScanButton onScan={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Scan" })).toBeNull();
  });
});
