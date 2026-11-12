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

/** Each contact's name by id, from the community's contacts; blank while they
 *  load or for a contact Hoard can no longer see. */
export function useContactNames(): (contactId: number | null | undefined) => string {
  const { data } = useContacts();
  return (contactId) => data?.find((contact) => contact.id === contactId)?.name ?? "";
}

/**
 * Picks an order's customer, a business, among the community's contacts in
 * Initiative. A name typed that no contact has is added as a new contact.
 * When the community has no contacts yet, a notice says so.
 */
export function CustomerPicker({
  value,
  onChange,
}: {
  value: number | null;
  onChange: (contactId: number) => void;
}) {
  const { t } = useTranslation("orders");
  // Why a contact couldn't be read or added is said in the connection's words.
  const { t: contactsT } = useTranslation("connection");
  const queries = useQueryClient();
  const contacts = useContacts();
  const add = useCreateContact();

  const addContact = async (name: string) => {
    try {
      const made = await add.mutateAsync({ data: { name } });
      queries.setQueryData<ContactRead[]>(getListContactsQueryKey(), (known) => [
        ...(known ?? []),
        made,
      ]);
      onChange(made.id);
    } catch (error) {
      toast.error(errorMessage(error, contactsT, "orders:customers.addFailed"));
      throw error;
    }
  };

  return (
    <Field
      label={t("customers.label")}
      hint={contacts.data?.length === 0 ? t("customers.noContacts") : undefined}
      error={
        contacts.isError
          ? errorMessage(contacts.error, contactsT, "orders:customers.loadFailed")
          : undefined
      }
    >
      <Combobox
        items={(contacts.data ?? []).map((contact) => ({
          value: String(contact.id),
          label: contact.name,
        }))}
        value={value === null ? null : String(value)}
        onValueChange={(next) => onChange(Number(next))}
        typedLabel={(typed) => (typed ? t("customers.add", { name: typed }) : "")}
        onCreate={addContact}
        loading={contacts.isLoading}
        loadingLabel={t("hoard:common.loading")}
        placeholder={t("customers.choose")}
        searchPlaceholder={t("customers.search")}
        emptyMessage={t("customers.none")}
      />
    </Field>
  );
}
