/**
 * What an app asks of the browser window it runs in: whether Initiative frames
 * it, and how to send the reader to another address. Tests set these, since
 * the window they run in is never framed and never navigates.
 */
export const browser = {
  /** Whether a window frames this page: Initiative, or its phone app. */
  framed: (): boolean => window.parent !== window,
  /** Leave the app's pages for `address`, such as Initiative's sign-in. */
  go: (address: string): void => window.location.assign(address),
};
