import i18n, { type BackendModule } from "i18next";
import { initReactI18next } from "react-i18next";

/**
 * Hoard's words (public/locales/<language>/hoard.json), the UI kit's
 * (packages/ui/public/locales) and the shared session's (packages/app, the
 * `app` namespace), side by side as namespaces. Each file is its
 * own chunk, loaded the first time a page reads its namespace in the reader's
 * language.
 *
 * Initiative decides the reader's language: the handoff carries it and a
 * message follows each change (src/session.tsx). The browser's language only
 * stands until the handoff arrives.
 */
const files = import.meta.glob<Record<string, unknown>>(
  [
    "../public/locales/*/*.json",
    "../../../../packages/ui/public/locales/*/*.json",
    "../../../../packages/app/public/locales/*/*.json",
  ],
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

/** The words a community may name for itself in Settings. */
export const COMMUNITY_WORDS = ["base", "recipe"] as const;
export type CommunityWord = (typeof COMMUNITY_WORDS)[number];

/** The community's own words; null for one it hasn't named. */
const named: Record<CommunityWord, string | null> = { base: null, recipe: null };

/** The event that follows a change to the community's words, which every
 *  page's words re-read on. */
const WORDS_CHANGED = "communityWordsChanged";

const capitalized = (word: string) =>
  word.charAt(0).toLocaleUpperCase(i18n.resolvedLanguage) + word.slice(1);

/**
 * `{{base}}`, `{{bases}}`, `{{recipe}}` and `{{recipes}}` in any of Hoard's
 * words, and each with a capital (`{{Recipe}}`) where a sentence starts with
 * it: the community's own word as typed, singular and plural alike, or, until
 * it names one, the reader's language's (`hoard:words.<word>`). Each is read
 * as a string is translated, so it follows the reader's language.
 */
const communityWords = Object.defineProperties(
  {},
  Object.fromEntries(
    COMMUNITY_WORDS.flatMap((word) =>
      [word, `${word}s`].flatMap((form) =>
        [false, true].map((capital) => [
          capital ? capitalized(form) : form,
          {
            enumerable: true,
            get: () => {
              const said =
                named[word] ?? i18n.t(`hoard:words.${form}`, { skipInterpolation: true });
              return capital ? capitalized(said) : said;
            },
          },
        ])
      )
    )
  )
);

/** Take the community's own words, from its settings; null for one it hasn't
 *  named. Every page's words follow. */
export function setCommunityWords(words: Record<CommunityWord, string | null>) {
  if (COMMUNITY_WORDS.every((word) => named[word] === words[word])) return;
  Object.assign(named, words);
  i18n.emit(WORDS_CHANGED);
}

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
    interpolation: { escapeValue: false, defaultVariables: communityWords },
    react: { bindI18n: `languageChanged ${WORDS_CHANGED}` },
  });

i18n.on("languageChanged", () => {
  document.documentElement.lang = i18n.resolvedLanguage ?? "en";
});

export default i18n;
