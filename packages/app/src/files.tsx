/**
 * The app's own files, such as a record's picture or an export, which its
 * pages load by their path under the app's API.
 *
 * In a browser the path is enough: the page and the API share an origin, and
 * the session's cookie goes with it. The phone app's pages are served from
 * the phone, and its session is a Bearer token that an `<img>` or a link
 * can't carry, so there the file is fetched through the app's API client and
 * shown from memory, or saved and handed to the phone's share sheet, which
 * saves it to the phone's files or opens it in another app.
 */

import { Directory, Filesystem } from "@capacitor/filesystem";
import { Share } from "@capacitor/share";
import {
  type AnchorHTMLAttributes,
  forwardRef,
  type ImgHTMLAttributes,
  type MouseEvent,
  useEffect,
  useState,
} from "react";

import { api } from "./auth";
import { isNative } from "./native/platform";

const fetchFile = async (path: string): Promise<Blob> =>
  (await api().get<Blob>(path, { responseType: "blob" })).data;

/**
 * Where an `<img>` loads the file at `path` from: the path itself in a
 * browser; on the phone, the file fetched with the session, from memory, once
 * it has arrived (undefined until then, and when it couldn't be read).
 */
export function useFileUrl(path: string | null): string | undefined {
  const [native] = useState(isNative);
  const [url, setUrl] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (!native || path === null) return;
    let made: string | undefined;
    let cancelled = false;
    fetchFile(path).then(
      (blob) => {
        if (cancelled) return;
        made = URL.createObjectURL(blob);
        setUrl(made);
      },
      () => {
        if (!cancelled) setUrl(undefined);
      }
    );
    return () => {
      cancelled = true;
      if (made) URL.revokeObjectURL(made);
      setUrl(undefined);
    };
  }, [native, path]);
  if (path === null) return undefined;
  return native ? url : path;
}

/** An `<img>` of the app's file at `path`, wherever the app runs. */
export function FileImage({
  path,
  ...image
}: { path: string } & Omit<ImgHTMLAttributes<HTMLImageElement>, "src">) {
  const src = useFileUrl(path);
  // An image without its file yet keeps its place and its alt text.
  // biome-ignore lint/a11y/useAltText: the caller's alt is spread in
  return <img src={src} {...image} />;
}

const asBase64 = (blob: Blob): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });

/** A file name the phone keeps: no folders, nothing it can't hold. */
const UNSAFE = new Set('\\/:*?"<>|');
const safeName = (name: string) =>
  Array.from(name, (ch) => (ch.charCodeAt(0) < 32 || UNSAFE.has(ch) ? "_" : ch))
    .join("")
    .slice(0, 120) || "file";

/** Fetch the app's file at `path` and hand it to the phone's share sheet as `name`. */
export async function shareFile(path: string, name: string): Promise<void> {
  const blob = await fetchFile(path);
  const written = await Filesystem.writeFile({
    path: safeName(name),
    data: await asBase64(blob),
    directory: Directory.Cache,
  });
  await Share.share({ files: [written.uri], title: name });
}

type FileLinkProps = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href" | "download"> & {
  /** The file's path under the app's API. */
  path: string;
  /** The name it is saved as; the server's when unset, in a browser. */
  fileName?: string;
  /** Called when the phone couldn't fetch or hand on the file. */
  onFailed?: (error: unknown) => void;
};

/**
 * A link that downloads the app's file at `path`: the browser's own download,
 * or, on the phone, the file fetched with the session and handed to the
 * phone's share sheet. Takes a button's look through `asChild`, as a link does.
 */
export const FileLink = forwardRef<HTMLAnchorElement, FileLinkProps>(function FileLink(
  { path, fileName, onFailed, onClick, ...link },
  ref
) {
  const [native] = useState(isNative);
  const click = (event: MouseEvent<HTMLAnchorElement>) => {
    onClick?.(event);
    if (!native || event.defaultPrevented) return;
    event.preventDefault();
    const name = fileName ?? path.split("/").filter(Boolean).pop() ?? "file";
    shareFile(path, name).catch((error: unknown) => onFailed?.(error));
  };
  return <a ref={ref} href={path} download={fileName ?? true} onClick={click} {...link} />;
});
