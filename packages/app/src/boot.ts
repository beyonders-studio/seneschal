import { isNative } from "./native/platform";
import { loadStorage } from "./native/storage";

/** What an app does before it renders: as its phone app, read what the phone
 *  keeps (its server and sign-in) into memory. */
export async function prepareApp(): Promise<void> {
  if (isNative()) await loadStorage();
}
