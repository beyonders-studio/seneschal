// The phone app, from apps/<app>/native.json and what every Seneschal
// app shares (packages/native).
import { nativeConfig } from "@seneschal/native/config";

import app from "../native.json" with { type: "json" };

export default nativeConfig(app);
