#!/usr/bin/env node
/**
 * Sign the phone app's web bundle an image carries, for the phone app to take
 * over the air (seneschal_core.native).
 *
 *   sign-ota.mjs <ota dir> <app> <version> <min native version> <key file>
 *
 * Writes <ota dir>/statement.json, `{v, app, version, sha256,
 * minNativeVersion}`, and statement.sig: its ECDSA P-256 signature over
 * SHA-256, IEEE P1363, base64, by the project's update key (a PKCS#8 PEM, in
 * Secret Manager, handed to the image build as a secret). The phone app
 * checks it against the public keys it was built with (packages/app
 * native/otaTrust.ts), and that it names the app, before it downloads the
 * zip. Without a key it writes nothing: the bundle is served unsigned, and no
 * phone app installs it.
 */

import { createHash, createPrivateKey, sign } from "node:crypto";
import { existsSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const [dir, app, version, minNativeVersion, keyFile] = process.argv.slice(2);
if (!dir || !app || !version || !minNativeVersion || !keyFile) {
  console.error("usage: sign-ota.mjs <ota dir> <app> <version> <min native version> <key file>");
  process.exit(2);
}

const pem = existsSync(keyFile) ? readFileSync(keyFile, "utf8").trim() : "";
if (!pem) {
  console.log("sign-ota: no update key, so the bundle is served unsigned");
  process.exit(0);
}

const sha256 = createHash("sha256")
  .update(readFileSync(path.join(dir, "bundle.zip")))
  .digest("hex");
const statement = JSON.stringify({ v: 2, app, version, sha256, minNativeVersion });
const signature = sign("sha256", Buffer.from(statement), {
  key: createPrivateKey(pem),
  dsaEncoding: "ieee-p1363",
}).toString("base64");
writeFileSync(path.join(dir, "statement.json"), statement);
writeFileSync(path.join(dir, "statement.sig"), `${signature}\n`);
console.log(`sign-ota: signed ${app} ${version} (${sha256})`);
