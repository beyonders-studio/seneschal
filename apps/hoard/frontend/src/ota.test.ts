import { DEV_KEYS, RELEASE_KEYS, verifiedStatement } from "@seneschal/app/native/otaTrust";
import { checkForUpdate, decideUpdate } from "@seneschal/app/native/update";
import axios from "axios";
import { beforeEach, describe, expect, it, vi } from "vitest";

const phone = vi.hoisted(() => ({
  info: { id: "studio.beyonders.hoard", version: "0.5.0" },
  downloaded: [] as { url: string; version: string; checksum: string }[],
}));
vi.mock("@capacitor/app", () => ({ App: { getInfo: async () => phone.info } }));
vi.mock("@capgo/capacitor-updater", () => ({
  CapacitorUpdater: {
    download: async (asked: { url: string; version: string; checksum: string }) => {
      phone.downloaded.push(asked);
      return { id: "b1", version: asked.version, status: "pending" };
    },
  },
}));

const toBase64 = (bytes: ArrayBuffer) => btoa(String.fromCharCode(...new Uint8Array(bytes)));

/** A P-256 key pair: its public half as base64 SPKI, and a signer. */
async function updateKey() {
  const pair = await crypto.subtle.generateKey({ name: "ECDSA", namedCurve: "P-256" }, true, [
    "sign",
    "verify",
  ]);
  const spki = toBase64(await crypto.subtle.exportKey("spki", pair.publicKey));
  // WebCrypto signs ECDSA as IEEE P1363, as sign-ota.mjs does.
  const sign = async (text: string) =>
    toBase64(
      await crypto.subtle.sign(
        { name: "ECDSA", hash: "SHA-256" },
        pair.privateKey,
        new TextEncoder().encode(text)
      )
    );
  return { spki, sign };
}

const statementOf = (fields: Record<string, unknown> = {}) =>
  JSON.stringify({
    v: 2,
    app: "hoard",
    version: "0.5.1",
    sha256: "ab".repeat(32),
    minNativeVersion: "0.5.0",
    ...fields,
  });

function serve(statement: string, signature: string, version = "0.5.1") {
  axios.defaults.adapter = async (config) => ({
    data: { version, url: "/api/v1/native/bundle/download", statement, signature },
    status: 200,
    statusText: "",
    headers: {},
    config,
  });
}

describe("Web bundles over the air", () => {
  beforeEach(() => {
    phone.info = { id: "studio.beyonders.hoard", version: "0.5.0" };
    phone.downloaded.length = 0;
  });

  it("takes a newer bundle its key signed for it, checking its digest", async () => {
    const key = await updateKey();
    const statement = statementOf();
    serve(statement, await key.sign(statement));

    const update = await checkForUpdate("https://hoard.example", "hoard", "0.5.0", [key.spki]);

    expect(update?.kind).toBe("ready");
    expect(phone.downloaded).toEqual([
      {
        url: "https://hoard.example/api/v1/native/bundle/download",
        version: "0.5.1",
        checksum: "ab".repeat(32),
      },
    ]);
  });

  it("ignores a bundle unsigned, signed by another key, altered, or another app's", async () => {
    const key = await updateKey();
    const stranger = await updateKey();
    const statement = statementOf();
    const theirs = statementOf({ app: "other" });

    serve(statement, "");
    expect(await checkForUpdate("https://h", "hoard", "0.5.0", [key.spki])).toBeNull();
    serve(statement, await stranger.sign(statement));
    expect(await checkForUpdate("https://h", "hoard", "0.5.0", [key.spki])).toBeNull();
    serve(statementOf({ sha256: "cd".repeat(32) }), await key.sign(statement));
    expect(await checkForUpdate("https://h", "hoard", "0.5.0", [key.spki])).toBeNull();
    serve(theirs, await key.sign(theirs));
    expect(await checkForUpdate("https://h", "hoard", "0.5.0", [key.spki])).toBeNull();
    expect(phone.downloaded).toEqual([]);
  });

  it("asks for a newer app when the bundle needs native code it lacks", async () => {
    const key = await updateKey();
    const statement = statementOf({ version: "0.6.0", minNativeVersion: "0.6.0" });
    serve(statement, await key.sign(statement), "0.6.0");

    const update = await checkForUpdate("https://h", "hoard", "0.5.0", [key.spki]);

    expect(update).toEqual({
      kind: "native-required",
      version: "0.6.0",
      minNativeVersion: "0.6.0",
    });
    expect(phone.downloaded).toEqual([]);
  });

  it("verifies only what the key signed", async () => {
    const key = await updateKey();
    const statement = statementOf();
    const signature = await key.sign(statement);

    expect(await verifiedStatement(statement, signature, [key.spki])).toMatchObject({
      app: "hoard",
    });
    expect(await verifiedStatement(statement, signature, [])).toBeNull();
  });

  it("decides what to do with a served bundle", () => {
    const at = { running: "0.5.0", native: "0.5.0", minNative: "0.5.0" };

    expect(decideUpdate({ ...at, served: "0.5.0" })).toBe("up-to-date");
    expect(decideUpdate({ ...at, served: "0.4.9" })).toBe("older-than-app");
    expect(decideUpdate({ ...at, served: "0.6.0", minNative: "0.6.0" })).toBe("native-required");
    expect(decideUpdate({ ...at, served: "0.5.1" })).toBe("download");
    expect(decideUpdate({ ...at, served: "0.5.0-dev.abc1234" })).toBe("download");
  });
});

describe("The keys a phone app is built to trust", () => {
  it("are P-256 public keys, a release key and its backup and a dev key", async () => {
    expect([RELEASE_KEYS.length, DEV_KEYS.length]).toEqual([2, 1]);
    for (const spki of [...RELEASE_KEYS, ...DEV_KEYS]) {
      const bytes = Uint8Array.from(atob(spki), (c) => c.charCodeAt(0));
      await crypto.subtle.importKey("spki", bytes, { name: "ECDSA", namedCurve: "P-256" }, false, [
        "verify",
      ]);
    }
  });

  it("never let a release app take a dev bundle", () => {
    expect(RELEASE_KEYS.filter((key) => DEV_KEYS.includes(key))).toEqual([]);
  });
});
