import i18n, { type BackendModule } from "i18next";
import { initReactI18next } from "react-i18next";

/**
 * Hoard's words (public/locales/<language>/hoard.json) and the UI kit's
 * (packages/ui/public/locales), side by side as namespaces. Each file is its
 * own chunk, loaded the first time a page reads its namespace in the reader's
 * language.
 *
 * Initiative decides the reader's language: the handoff carries it and a
 * message follows each change (src/session.tsx). The browser's language only
 * stands until the handoff arrives.
 */
const files = import.meta.glob<Record<string, unknown>>(
  ["../public/locales/*/*.json", "../../../../packages/ui/public/locales/*/*.json"],
  { import: "default" }
);

/** Each file's loader, by `<language>/<namespace>`. */
const words = new Map(
  Object.entries(files).map(([file, load]) => [
    file
      .split("/")
      .slice(-2)
      .join("/")
      .replace(/\.json$/, ""),
    load,
  ])
);

const bundled: BackendModule = {
  type: "backend",
  init: () => {},
  read: (language, namespace, done) => {
    const load = words.get(`${language}/${namespace}`);
    // A language a namespace isn't in yet reads as empty, and falls back to English.
    if (!load) return done(null, {});
    load().then(
      (loaded) => done(null, loaded),
      (error: Error) => done(error, null)
    );
  },
};

void i18n
  .use(bundled)
  .use(initReactI18next)
  .init({
    lng: navigator.language,
    load: "languageOnly",
    fallbackLng: "en",
    supportedLngs: [...new Set([...words.keys()].map((key) => key.split("/")[0]))],
    nonExplicitSupportedLngs: true,
    defaultNS: "hoard",
    ns: ["hoard"],
    interpolation: { escapeValue: false },
  });

i18n.on("languageChanged", () => {
  document.documentElement.lang = i18n.resolvedLanguage ?? "en";
});

export default i18n;
