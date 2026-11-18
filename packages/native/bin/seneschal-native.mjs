#!/usr/bin/env node
/**
 * Makes a Seneschal app's phone app from what every one shares.
 *
 *   seneschal-native new <app>     add apps/<app>/frontend/android, then apply
 *   seneschal-native apply <app>   (re)apply the shared pieces; safe to repeat
 *
 * An app needs apps/<app>/native.json (its id, name, URL scheme and cloud
 * address) and apps/<app>/VERSION. `new` writes its capacitor.config.ts, adds
 * the Android project with Capacitor's CLI, and applies:
 *
 * - packages/native/android/seneschal-app.gradle, which sets the id, name,
 *   version and signing from the app's files and the build's properties;
 * - in AndroidManifest.xml: the name the build sets, its URL scheme (where the
 *   system browser hands a sign-in back), unencrypted HTTP in debug builds
 *   alone, and the backup rules;
 * - backup rules that leave the credentials out of the phone's backups and
 *   device transfers (the SeneschalCredentials Preferences group,
 *   packages/app/src/native/storage.ts), so a restored phone signs in again;
 * - for an app that scans with the camera, the code scanner module Google
 *   Play services installs with it;
 * - its icon and splash, from apps/<app>/frontend/assets when they are there.
 */

import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import path from "node:path";

const ROOT = path.resolve(import.meta.dirname, "../../..");
const SHARED_GRADLE = "packages/native/android/seneschal-app.gradle";
const CREDENTIALS = "SeneschalCredentials.xml";

const fail = (message) => {
  console.error(`seneschal-native: ${message}`);
  process.exit(1);
};

const [command, app] = process.argv.slice(2);
if (!["new", "apply"].includes(command) || !/^[a-z][a-z0-9-]*$/.test(app ?? "")) {
  fail("usage: seneschal-native new|apply <app>");
}

const appDir = path.join(ROOT, "apps", app);
const frontend = path.join(appDir, "frontend");
const android = path.join(frontend, "android");
const nativeJson = path.join(appDir, "native.json");
if (!existsSync(nativeJson)) fail(`apps/${app}/native.json is missing`);
const native = JSON.parse(readFileSync(nativeJson, "utf8"));
for (const field of ["appId", "name", "scheme", "cloudOrigin"]) {
  if (typeof native[field] !== "string" || !native[field]) fail(`native.json needs "${field}"`);
}

const run = (args, cwd = frontend) =>
  execFileSync("pnpm", ["exec", ...args], { cwd, stdio: "inherit" });

if (command === "new") {
  if (existsSync(android)) fail(`apps/${app}/frontend/android exists; use apply`);
  const config = path.join(frontend, "capacitor.config.ts");
  if (!existsSync(config)) {
    writeFileSync(
      config,
      [
        "// The phone app, from apps/<app>/native.json and what every Seneschal",
        "// app shares (packages/native).",
        'import { nativeConfig } from "@seneschal/native/config";',
        "",
        'import app from "../native.json" with { type: "json" };',
        "",
        "export default nativeConfig(app);",
        "",
      ].join("\n")
    );
  }
  // Capacitor copies the built pages in; the first add needs somewhere to copy from.
  const dist = path.join(frontend, "dist");
  if (!existsSync(dist)) {
    mkdirSync(dist);
    writeFileSync(path.join(dist, "index.html"), "<!doctype html><title>build me</title>\n");
  }
  run(["cap", "add", "android"]);
}

if (!existsSync(android)) fail(`apps/${app}/frontend/android is missing; use new`);

/** A Gradle manifest placeholder, which the build fills in: `${name}`. */
const gradle = (name) => ["$", "{", name, "}"].join("");

/** Replace `pattern` in `file` by `replacement` once, unless `done` is already there. */
const patch = (file, done, pattern, replacement) => {
  const text = readFileSync(file, "utf8");
  if (text.includes(done)) return;
  if (!pattern.test(text)) fail(`${path.relative(ROOT, file)}: can't find ${pattern}`);
  writeFileSync(file, text.replace(pattern, replacement));
};

// The shared build, applied last so what it sets stands.
const buildGradle = path.join(android, "app/build.gradle");
const relativeShared = path.relative(path.dirname(buildGradle), path.join(ROOT, SHARED_GRADLE));
patch(
  buildGradle,
  SHARED_GRADLE.split("/").pop(),
  /\s*$/,
  `\n\n// What every Seneschal phone app's build shares: id, name, version, signing.\napply from: "${relativeShared}"\n`
);

const manifest = path.join(android, "app/src/main/AndroidManifest.xml");
patch(
  manifest,
  gradle("appLabel"),
  /android:label="@string\/app_name"/,
  `android:label="${gradle("appLabel")}"`
);
patch(
  manifest,
  "usesCleartextTraffic",
  /<application\b/,
  [
    "<application",
    `        android:usesCleartextTraffic="${gradle("usesCleartextTraffic")}"`,
    '        android:dataExtractionRules="@xml/data_extraction_rules"',
    '        android:fullBackupContent="@xml/backup_rules"',
  ].join("\n")
);
patch(
  manifest,
  gradle("appScheme"),
  /(<activity[\s\S]*?android:name="\.MainActivity"[\s\S]*?<\/intent-filter>)/,
  `$1

            <!-- Where the system browser hands a sign-in back. -->
            <intent-filter>
                <action android:name="android.intent.action.VIEW" />
                <category android:name="android.intent.category.DEFAULT" />
                <category android:name="android.intent.category.BROWSABLE" />
                <data android:scheme="${gradle("appScheme")}" />
            </intent-filter>`
);

// An app that scans with the camera asks Google Play services for its code
// scanner module when it is installed (packages/app/src/scan.tsx).
const dependencies = JSON.parse(readFileSync(path.join(frontend, "package.json"), "utf8"));
if (dependencies.dependencies?.["@capacitor-mlkit/barcode-scanning"]) {
  patch(
    manifest,
    "com.google.mlkit.vision.DEPENDENCIES",
    /(\n\s*)<\/application>/,
    `$1    <!-- Google's code scanner, which scans with the camera (packages/app/src/scan.tsx). -->
        <meta-data android:name="com.google.mlkit.vision.DEPENDENCIES" android:value="barcode_ui" />$1</application>`
  );
}

const xml = path.join(android, "app/src/main/res/xml");
mkdirSync(xml, { recursive: true });
writeFileSync(
  path.join(xml, "backup_rules.xml"),
  `<?xml version="1.0" encoding="utf-8"?>
<!-- Android 11 and older: the credentials and the web view's data stay out of backups. -->
<full-backup-content>
    <exclude domain="sharedpref" path="${CREDENTIALS}" />
    <exclude domain="root" path="app_webview/" />
</full-backup-content>
`
);
writeFileSync(
  path.join(xml, "data_extraction_rules.xml"),
  `<?xml version="1.0" encoding="utf-8"?>
<!-- Android 12 and newer: the credentials and the web view's data stay out of
     cloud backups and device transfers, so a restored phone signs in again. -->
<data-extraction-rules>
    <cloud-backup>
        <exclude domain="sharedpref" path="${CREDENTIALS}" />
        <exclude domain="root" path="app_webview/" />
    </cloud-backup>
    <device-transfer>
        <exclude domain="sharedpref" path="${CREDENTIALS}" />
        <exclude domain="root" path="app_webview/" />
    </device-transfer>
</data-extraction-rules>
`
);

if (existsSync(path.join(frontend, "assets/icon-only.png"))) {
  run(["capacitor-assets", "generate", "--android"]);
} else {
  console.log(`no apps/${app}/frontend/assets/icon-only.png: Capacitor's default icon stays`);
}

console.log(`apps/${app}: the phone app has what every Seneschal app shares.`);
