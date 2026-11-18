/**
 * What every Seneschal phone app's Capacitor config holds, made from the app's
 * `native.json`. An app's `capacitor.config.ts` is:
 *
 *   import { nativeConfig } from "@seneschal/native/config";
 *   import app from "../native.json" with { type: "json" };
 *   export default nativeConfig(app);
 *
 * CommonJS, because Capacitor's CLI requires the config it transpiles.
 *
 * @param {import("./config.d.cts").NativeApp} app
 * @returns {import("@capacitor/cli").CapacitorConfig}
 */
function nativeConfig(app) {
  return {
    appId: app.appId,
    appName: app.name,
    webDir: "dist",
    server: {
      // The pages are served at https://<appId>, the origin the app's server
      // lets call its API (seneschal_core.sign_in.phone_app_origins).
      hostname: app.appId,
      androidScheme: "https",
      iosScheme: "https",
    },
    // Nothing from the bridge or the page's console goes to the device log.
    loggingBehavior: "none",
    android: { allowMixedContent: false },
  };
}

module.exports = { nativeConfig };
