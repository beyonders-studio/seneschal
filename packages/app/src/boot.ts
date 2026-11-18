import { isNative } from "./native/platform";
import { loadStorage } from "./native/storage";
import { startedWell } from "./native/update";

/** What an app does before it renders: as its phone app, read what the phone
 *  keeps (its server and sign-in) into memory, and keep the web bundle that
 *  started, so one taken over the air isn't rolled back. */
export async function prepareApp(): Promise<void> {
  if (!isNative()) return;
  await loadStorage();
  await startedWell().catch(() => undefined);
}
