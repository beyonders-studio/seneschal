import type { CapacitorConfig } from "@capacitor/cli";

/** An app's `native.json`: who its phone app is. */
export interface NativeApp {
  /** The Android application id and iOS bundle id: `studio.beyonders.hoard`. */
  appId: string;
  /** Its name under its icon. */
  name: string;
  /** The URL scheme the system browser hands its sign-ins back by: `hoard`. */
  scheme: string;
  /** Its address on Initiative's cloud. */
  cloudOrigin: string;
  /** The Apple team that signs it, for when it ships on iOS. */
  iosTeam?: string;
}

export function nativeConfig(app: NativeApp): CapacitorConfig;
