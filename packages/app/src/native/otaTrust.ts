/**
 * Which web bundles a phone app takes over the air: only those whose statement
 * a key it was built with signed (`packages/native/bin/sign-ota.mjs`), and
 * that name the app.
 *
 * The keys are the project's, kept in Secret Manager and used only by the
 * image builds (the infra repository's `scripts/keys/ota-signing-key.sh`).
 * A release app trusts the release key and its offline backup, so the release
 * key can be replaced without stranding installed apps. A dev app
 * (`<appId>.dev`) trusts the dev key alone, so a dev image's bundle never
 * reaches a release app.
 */

/** Base64 SPKI DER of the release key, then its backup. */
export const RELEASE_KEYS: readonly string[] = [
  "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAE40gXx4/OQPjSmaLP3+A0Efw59K5DvKuZkvxLcl1kvkWLU5jiXMvtIGUM79DGOClqH/iHALkoVlMLkbZqa5Y2DQ==",
  // The offline backup.
  "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEpx1ux+HGakJR308O/uC123LzhW2ITW18CL88etWXo661MiFlnoTcnU7jotzRYtDD/a42Rh5ciEdLCH6DCfSbPA==",
];

/** Base64 SPKI DER of the dev key. */
export const DEV_KEYS: readonly string[] = [
  "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEw+6wajyLiaiEK4cpXKh4nu4oWR+yr6hkKeqHGbC16YaOYBXoTbSMjPjwh00HVY8/g0X4/MdwsLzt1TkYzvRr+A==",
];

/** The keys an app with the id `appId` trusts. */
export const keysFor = (appId: string): readonly string[] =>
  appId.endsWith(".dev") ? DEV_KEYS : RELEASE_KEYS;

/** What a bundle's statement says, once its signature holds. */
export interface UpdateStatement {
  v: 2;
  /** The app it is for (`hoard`). */
  app: string;
  version: string;
  /** SHA-256 of the zip, hex. */
  sha256: string;
  /** The oldest phone app it runs on. */
  minNativeVersion: string;
}

const fromBase64 = (value: string) => Uint8Array.from(atob(value), (c) => c.charCodeAt(0));

/** The statement, when one of `keys` signed it; null otherwise. */
export async function verifiedStatement(
  statement: string,
  signature: string,
  keys: readonly string[]
): Promise<UpdateStatement | null> {
  const data = new TextEncoder().encode(statement);
  for (const spki of keys) {
    try {
      const key = await crypto.subtle.importKey(
        "spki",
        fromBase64(spki),
        { name: "ECDSA", namedCurve: "P-256" },
        false,
        ["verify"]
      );
      const holds = await crypto.subtle.verify(
        { name: "ECDSA", hash: "SHA-256" },
        key,
        fromBase64(signature),
        data
      );
      if (holds) {
        const parsed = JSON.parse(statement) as UpdateStatement;
        return parsed.v === 2 ? parsed : null;
      }
    } catch {
      // A key or a signature that doesn't decode verifies nothing.
    }
  }
  return null;
}
