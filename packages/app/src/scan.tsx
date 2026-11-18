/**
 * Scanning a barcode with the camera, wherever the app runs.
 *
 * - **As the phone app:** Google's code scanner (ML Kit, through
 *   `@capacitor-mlkit/barcode-scanning`), a full-screen scanner of its own
 *   that reads on the phone and needs no camera permission. Its module comes
 *   with Google Play services; the first scan on a phone without it asks for
 *   it, and the person tries again once it's there.
 * - **In a browser** that has the Barcode Detection API (Chrome on Android
 *   and macOS): the camera in a dialog, read frame by frame.
 * - **Elsewhere** (Safari, Firefox, desktop Chrome on Windows and Linux): no
 *   camera scanning, so no Scan button; typing a code works everywhere.
 *
 * Either way the page gets the scan's raw text, which the app reads with its
 * scan route (GS1 labels, retail barcodes, its own codes).
 */

import { BarcodeFormat, BarcodeScanner } from "@capacitor-mlkit/barcode-scanning";
import { ScanLine } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import { Button } from "@/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/ui/dialog";
import { host } from "@/ui/host";

import { isNative } from "./native/platform";

/** The symbols a label or a product carries. */
const FORMATS = [
  BarcodeFormat.DataMatrix,
  BarcodeFormat.QrCode,
  BarcodeFormat.Code128,
  BarcodeFormat.Ean13,
  BarcodeFormat.Ean8,
  BarcodeFormat.UpcA,
  BarcodeFormat.UpcE,
  BarcodeFormat.Itf,
];

/** The same, as the Barcode Detection API names them. */
const BROWSER_FORMATS = [
  "data_matrix",
  "qr_code",
  "code_128",
  "ean_13",
  "ean_8",
  "upc_a",
  "upc_e",
  "itf",
];

/** How often a browser looks at the camera's picture for a barcode. */
const LOOK_EVERY_MS = 200;

interface DetectedBarcode {
  rawValue: string;
}

interface BarcodeDetectorLike {
  detect(source: CanvasImageSource): Promise<DetectedBarcode[]>;
}

type BarcodeDetectorClass = new (options?: { formats?: string[] }) => BarcodeDetectorLike;

const browserDetector = (): BarcodeDetectorClass | null => {
  if (typeof window === "undefined") return null;
  const detector = (window as unknown as { BarcodeDetector?: BarcodeDetectorClass })
    .BarcodeDetector;
  return detector && typeof navigator.mediaDevices?.getUserMedia === "function" ? detector : null;
};

/** Why the camera couldn't scan, in the app's words. */
export class ScanUnavailable extends Error {
  constructor(readonly reason: "preparing" | "denied" | "failed") {
    super(reason);
  }
}

/** How the camera scans here: the phone app's scanner, a browser's, or none. */
export const cameraScanning = (): "native" | "browser" | null =>
  isNative() ? "native" : browserDetector() ? "browser" : null;

/**
 * Scan with the phone app's scanner: the barcode's raw text, or null when
 * the person closed it without scanning.
 */
export async function scanOnPhone(): Promise<string | null> {
  const { available } = await BarcodeScanner.isGoogleBarcodeScannerModuleAvailable();
  if (!available) {
    // Asked for once; Google Play services fetches it in the background.
    await BarcodeScanner.installGoogleBarcodeScannerModule().catch(() => undefined);
    throw new ScanUnavailable("preparing");
  }
  try {
    const { barcodes } = await BarcodeScanner.scan({ formats: FORMATS, autoZoom: true });
    return barcodes[0]?.rawValue ?? null;
  } catch (error) {
    if (error instanceof Error && /cancel/i.test(error.message)) return null;
    throw new ScanUnavailable("failed");
  }
}

/** The camera, in a dialog, until a barcode is seen or the person closes it. */
function BrowserScanner({
  onScanned,
  onClose,
}: {
  onScanned: (text: string) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation("app");
  const video = useRef<HTMLVideoElement>(null);
  const [problem, setProblem] = useState<string | null>(null);
  // The latest handler, so a new one doesn't restart the camera.
  const scanned = useRef(onScanned);
  scanned.current = onScanned;

  useEffect(() => {
    const Detector = browserDetector();
    if (!Detector) return;
    let stream: MediaStream | null = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let done = false;
    const detector = new Detector({ formats: BROWSER_FORMATS });
    const look = async () => {
      if (done || !video.current) return;
      try {
        const [seen] = await detector.detect(video.current);
        if (seen?.rawValue && !done) {
          done = true;
          scanned.current(seen.rawValue);
          return;
        }
      } catch {
        // A frame not ready yet: look again.
      }
      timer = setTimeout(look, LOOK_EVERY_MS);
    };
    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" }, audio: false })
      .then(async (opened) => {
        stream = opened;
        if (done || !video.current) return;
        video.current.srcObject = opened;
        await video.current.play();
        void look();
      })
      .catch((error: unknown) =>
        setProblem(
          t(
            error instanceof DOMException && error.name === "NotAllowedError"
              ? "scan.denied"
              : "scan.failed"
          )
        )
      );
    return () => {
      done = true;
      clearTimeout(timer);
      for (const track of stream?.getTracks() ?? []) track.stop();
    };
  }, [t]);

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogTitle>{t("scan.title")}</DialogTitle>
        <DialogDescription>{problem ?? t("scan.hint")}</DialogDescription>
        {!problem && (
          <video
            ref={video}
            className="aspect-square w-full rounded-md bg-black object-cover"
            muted
            playsInline
          />
        )}
        <Button variant="outline" onClick={onClose}>
          {t("scan.cancel")}
        </Button>
      </DialogContent>
    </Dialog>
  );
}

/**
 * A button that scans a barcode with the camera and hands its raw text to
 * `onScan`. Drawn only where the camera can scan.
 */
export function ScanButton({
  onScan,
  label,
  className,
  size = "icon",
}: {
  onScan: (text: string) => void;
  /** What the button is called; "Scan" when not given. */
  label?: string;
  className?: string;
  size?: "icon" | "sm" | "default";
}) {
  const { t } = useTranslation("app");
  const [how] = useState(cameraScanning);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  if (how === null) return null;
  const name = label ?? t("scan.button");

  const scan = async () => {
    if (how === "browser") {
      setOpen(true);
      return;
    }
    setBusy(true);
    try {
      const text = await scanOnPhone();
      if (text) onScan(text);
    } catch (error) {
      host.toast.error(
        t(error instanceof ScanUnavailable ? `scan.${error.reason}` : "scan.failed")
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size={size}
        className={className}
        aria-label={name}
        disabled={busy}
        onClick={() => void scan()}
      >
        <ScanLine aria-hidden />
        {size !== "icon" && name}
      </Button>
      {open && (
        <BrowserScanner
          onClose={() => setOpen(false)}
          onScanned={(text) => {
            setOpen(false);
            onScan(text);
          }}
        />
      )}
    </>
  );
}

// --- handheld scanners ---------------------------------------------------------

/** The longest pause between a scanner's keystrokes; a person types slower. */
const SCANNER_GAP_MS = 40;
/** The fewest characters a scan holds. */
const SCANNER_MIN_LENGTH = 4;

type ScanTarget = { current: (text: string) => void };

/** The pages' scan targets, the latest shown last: it gets each scan. */
const targets: ScanTarget[] = [];

/**
 * Take what a handheld scanner reads while this is shown: the latest shown
 * target gets each scan, so a dialog takes it from its page, and a page from
 * the search field every page shows. Null takes none.
 */
export function useScanTarget(onScan: ((text: string) => void) | null): void {
  const latest = useRef(onScan);
  latest.current = onScan;
  const taking = onScan !== null;
  useEffect(() => {
    if (!taking) return;
    const target: ScanTarget = { current: (text) => latest.current?.(text) };
    targets.push(target);
    return () => {
      targets.splice(targets.indexOf(target), 1);
    };
  }, [taking]);
}

const editable = (element: EventTarget | null) =>
  element instanceof HTMLElement &&
  (element.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName));

/**
 * A handheld scanner in keyboard mode, USB or Bluetooth, a phone's included,
 * types its scan: faster than a person can, ending in Enter or Tab. Outside a
 * field, such a burst goes to the latest scan target instead of the page; in
 * a field it types there, as it always has. Mounted once, by the session.
 */
export function HandheldScanner() {
  useEffect(() => {
    let typed = "";
    let last = 0;
    let slow = false;
    const key = (event: KeyboardEvent) => {
      if (editable(event.target) || event.ctrlKey || event.metaKey || event.altKey) {
        typed = "";
        return;
      }
      const now = Date.now();
      if (now - last > SCANNER_GAP_MS) {
        typed = "";
        slow = false;
      }
      last = now;
      if (event.key === "Enter" || event.key === "Tab") {
        const scan = typed;
        typed = "";
        const target = targets.at(-1);
        if (!slow && scan.length >= SCANNER_MIN_LENGTH && target) {
          event.preventDefault();
          target.current(scan);
        }
        return;
      }
      if (event.key.length === 1) typed += event.key;
      else if (event.key !== "Shift") slow = true;
    };
    window.addEventListener("keydown", key, true);
    return () => window.removeEventListener("keydown", key, true);
  }, []);
  return null;
}
