/**
 * Web bundles over the air, as Initiative's app takes them (its
 * `useNativeUpdate`): the server's image carries the phone app's pages
 * (`seneschal_core.native`), and the app takes a newer bundle, once its
 * statement is signed by a key it trusts (`otaTrust.ts`) and names it, when
 * the bundle doesn't need native code the installed app lacks. Capgo's
 * updater downloads it, checking its SHA-256, and swaps it in on a restart the
 * person chooses; a swapped-in bundle that doesn't start rolls back by itself.
 */

import { App } from "@capacitor/app";
import { type BundleInfo, CapacitorUpdater } from "@capgo/capacitor-updater";
import axios from "axios";

import { keysFor, verifiedStatement } from "./otaTrust";

interface Manifest {
  version: string;
  url: string;
  statement: string | null;
  signature: string | null;
}

/** What a phone app should do with the bundle its server offers. */
export type Update =
  | { kind: "ready"; version: string; bundle: BundleInfo }
  | { kind: "native-required"; version: string; minNativeVersion: string };

/** `major.minor.patch` as numbers; a `-suffix` is left out. */
const core = (version: string): number[] =>
  version
    .split("-")[0]
    .split(".")
    .map((part) => Number.parseInt(part, 10) || 0);

/** Below zero when `a` comes before `b`, by `major.minor.patch` alone. */
export function compareCore(a: string, b: string): number {
  const [x, y] = [core(a), core(b)];
  for (let i = 0; i < 3; i += 1) {
    if ((x[i] ?? 0) !== (y[i] ?? 0)) return (x[i] ?? 0) - (y[i] ?? 0);
  }
  return 0;
}

/**
 * What to do with a served bundle. Pure, so it can be tested.
 * - `up-to-date`: it is the bundle running;
 * - `older-than-app`: it is older than the one the app was built with;
 * - `native-required`: it needs a newer phone app than the one installed;
 * - `download`: take it.
 */
export function decideUpdate(args: {
  served: string;
  running: string;
  native: string;
  minNative: string;
}): "up-to-date" | "older-than-app" | "native-required" | "download" {
  if (args.served === args.running) return "up-to-date";
  if (compareCore(args.served, args.native) < 0) return "older-than-app";
  if (compareCore(args.native, args.minNative) < 0) return "native-required";
  return "download";
}

/**
 * Ask `server` for its bundle, and download it when it is the app's to take.
 * `app` is the app's own name (`hoard`), and `running` the version of the
 * pages running now; `trusted` the keys it trusts, its own unless given.
 * Null when there is nothing to do, or the bundle isn't one the app trusts.
 */
export async function checkForUpdate(
  server: string,
  app: string,
  running: string,
  trusted?: readonly string[]
): Promise<Update | null> {
  let manifest: Manifest;
  try {
    manifest = (
      await axios.get<Manifest>(`${server}/api/v1/native/bundle/manifest`, { timeout: 10_000 })
    ).data;
  } catch {
    return null;
  }
  if (!manifest.statement || !manifest.signature) return null;
  const info = await App.getInfo();
  const statement = await verifiedStatement(
    manifest.statement,
    manifest.signature,
    trusted ?? keysFor(info.id)
  );
  if (!statement || statement.app !== app || statement.version !== manifest.version) return null;
  const decided = decideUpdate({
    served: statement.version,
    running,
    native: info.version,
    minNative: statement.minNativeVersion,
  });
  if (decided === "native-required") {
    return {
      kind: "native-required",
      version: statement.version,
      minNativeVersion: statement.minNativeVersion,
    };
  }
  if (decided !== "download") return null;
  const bundle = await CapacitorUpdater.download({
    url: new URL(manifest.url, server).toString(),
    version: statement.version,
    checksum: statement.sha256,
  });
  return { kind: "ready", version: statement.version, bundle };
}

/** Restart into a downloaded bundle. */
export const install = (bundle: BundleInfo) => CapacitorUpdater.set({ id: bundle.id });

/** The bundle running started: keep it, rather than roll back. */
export const startedWell = () => CapacitorUpdater.notifyAppReady();
