import { Capacitor } from "@capacitor/core";

/** Whether the app runs as its phone app, rather than in a browser. */
export const isNative = (): boolean => Capacitor.isNativePlatform();
