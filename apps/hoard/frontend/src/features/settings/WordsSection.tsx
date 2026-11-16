import { useMutation, useQueryClient } from "@tanstack/react-query";
import { type FormEvent, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { SettingsRead } from "@/api/generated/hoard.schemas";
import {
  getReadSettingsQueryKey,
  updateSettings,
  useReadSettings,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { orNull } from "@/features/items/format";
import { useChangesSettings } from "@/features/settings/NamedList";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { COMMUNITY_WORDS, type CommunityWord, setCommunityWords } from "@/i18n";
import { errorMessage } from "@/lib/errors";
import { Button } from "@/ui/button";
import { Field } from "@/ui/field";
import { Input } from "@/ui/input";

const setting = (word: CommunityWord) => `${word}_word` as const;

const wordsOf = (settings: SettingsRead) =>
  Object.fromEntries(
    COMMUNITY_WORDS.map((word) => [word, settings[setting(word)] ?? null])
  ) as Record<CommunityWord, string | null>;

/** Takes the community's own words for a base and a recipe from its
 *  settings, which every page then says (`src/i18n.ts`). */
export function CommunityWords() {
  const settings = useReadSettings().data;
  useEffect(() => {
    if (settings) setCommunityWords(wordsOf(settings));
  }, [settings]);
  return null;
}

/** What the community calls a base and a recipe, which an admin names; each
 *  left empty is Hoard's own word in the reader's language. */
export function WordsSection() {
  const { t } = useTranslation("catalog");
  const settings = useReadSettings().data;
  const changes = useChangesSettings();
  if (!settings) return null;
  const words = wordsOf(settings);
  return (
    <SettingsSection id="words" title={t("settings.words.title")}>
      <p className="text-sm text-muted-foreground">{t("settings.words.description")}</p>
      {changes ? (
        <WordsForm key={JSON.stringify(words)} current={words} />
      ) : (
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
          {COMMUNITY_WORDS.map((word) => (
            <div key={word} className="contents">
              <dt className="text-muted-foreground">{t(`settings.words.${word}`)}</dt>
              <dd>{words[word] ?? t(`hoard:words.${word}`)}</dd>
            </div>
          ))}
        </dl>
      )}
    </SettingsSection>
  );
}

function WordsForm({ current }: { current: Record<CommunityWord, string | null> }) {
  const { t } = useTranslation("catalog");
  const queryClient = useQueryClient();
  const [typed, setTyped] = useState(() =>
    Object.fromEntries(COMMUNITY_WORDS.map((word) => [word, current[word] ?? ""]))
  );
  const changed = COMMUNITY_WORDS.filter((word) => orNull(typed[word]) !== current[word]);
  const save = useMutation({
    mutationFn: () =>
      updateSettings(
        Object.fromEntries(changed.map((word) => [setting(word), orNull(typed[word])]))
      ),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: getReadSettingsQueryKey() });
      toast.success(t("settings.words.saved"));
    },
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    save.mutate();
  };
  return (
    <form onSubmit={submit} className="flex flex-wrap items-end gap-2">
      {COMMUNITY_WORDS.map((word) => (
        <Field
          key={word}
          label={t(`settings.words.${word}`)}
          hint={t(`settings.words.${word}Hint`)}
        >
          <Input
            value={typed[word]}
            maxLength={100}
            className="w-56"
            placeholder={t(`hoard:words.${word}`)}
            onChange={(event) => setTyped({ ...typed, [word]: event.target.value })}
          />
        </Field>
      ))}
      <Button type="submit" pending={save.isPending} disabled={changed.length === 0}>
        {t("hoard:common.save")}
      </Button>
    </form>
  );
}
