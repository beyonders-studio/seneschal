import { useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import {
  getListContactsQueryKey,
  useCreateContact,
  useListContacts,
} from "@/api/generated/contacts/contacts";
import type { ContactRead } from "@/api/generated/hoard.schemas";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { Combobox } from "@/ui/combobox";
import { Field } from "@/ui/field";

/** How long the community's contacts are used before they are asked for again. */
const CONTACTS_FRESH_MS = 60_000;

const useContacts = () => useListContacts({ query: { staleTime: CONTACTS_FRESH_MS } });

/** Each supplier's name by contact id, from the community's contacts; blank
 *  while they load or for a contact Hoard can no longer see. */
export function useSupplierNames(): (contactId: number | null | undefined) => string {
  const { data } = useContacts();
  return (contactId) => data?.find((contact) => contact.id === contactId)?.name ?? "";
}

/**
 * Picks a supplier among the community's contacts in Initiative, under
 * `label` when it is not a purchase's.
 * A name typed that no contact has is added as a new contact. When the
 * community has no contacts yet, a notice says so.
 */
export function SupplierPicker({
  label,
  value,
  onChange,
}: {
  label?: string;
  value: number | null;
  onChange: (contactId: number) => void;
}) {
  const { t } = useTranslation("connection");
  const queries = useQueryClient();
  const contacts = useContacts();
  const add = useCreateContact();

  const addSupplier = async (name: string) => {
    try {
      const made = await add.mutateAsync({ data: { name } });
      queries.setQueryData<ContactRead[]>(getListContactsQueryKey(), (known) => [
        ...(known ?? []),
        made,
      ]);
      onChange(made.id);
    } catch (error) {
      toast.error(errorMessage(error, t, "suppliers.addFailed"));
      throw error;
    }
  };

  return (
    <Field
      label={label ?? t("suppliers.label")}
      hint={contacts.data?.length === 0 ? t("suppliers.noContacts") : undefined}
      error={contacts.isError ? errorMessage(contacts.error, t, "suppliers.loadFailed") : undefined}
    >
      <Combobox
        items={(contacts.data ?? []).map((contact) => ({
          value: String(contact.id),
          label: contact.name,
        }))}
        value={value === null ? null : String(value)}
        onValueChange={(next) => onChange(Number(next))}
        typedLabel={(typed) => (typed ? t("suppliers.add", { name: typed }) : "")}
        onCreate={addSupplier}
        loading={contacts.isLoading}
        loadingLabel={t("hoard:common.loading")}
        placeholder={t("suppliers.choose")}
        searchPlaceholder={t("suppliers.search")}
        emptyMessage={t("suppliers.none")}
      />
    </Field>
  );
}
