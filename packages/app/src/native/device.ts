/** What the phone itself tells the app: links it opens, and its back button. */

import { App } from "@capacitor/app";

/** Call `open` with each address the phone opens the app at, the one it
 *  started with first. Returns the undo. */
export function listenForLinks(open: (url: string) => void): () => void {
  const listening = App.addListener("appUrlOpen", ({ url }) => open(url));
  void App.getLaunchUrl().then((launch) => {
    if (launch?.url) open(launch.url);
  });
  return () => void listening.then((handle) => handle.remove());
}

/** Android's back button goes back a page, and leaves the app from the first. */
export function followBackButton(): () => void {
  const listening = App.addListener("backButton", ({ canGoBack }) => {
    if (canGoBack) window.history.back();
    else void App.exitApp();
  });
  return () => void listening.then((handle) => handle.remove());
}
