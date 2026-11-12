import { useQueryClient } from "@tanstack/react-query";
import { X } from "lucide-react";
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
 * Picks an order's customer, a business, or a shipment's carrier (`of`)
 * among the community's contacts in Initiative. A name typed that no contact
 * has is added as a new contact. When the community has no contacts yet, a
 * notice says so. A carrier may be cleared; a customer is always picked.
 */
export function ContactPicker({
  of,
  value,
  onChange,
}: {
  of: "customers" | "carriers";
  value: number | null;
  onChange: (contactId: number | null) => void;
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
      toast.error(errorMessage(error, contactsT, `orders:${of}.addFailed`));
      throw error;
    }
  };

  return (
    <Field
      label={t(`${of}.label`)}
      hint={contacts.data?.length === 0 ? t(`${of}.noContacts`) : undefined}
      error={
        contacts.isError
          ? errorMessage(contacts.error, contactsT, `orders:${of}.loadFailed`)
          : undefined
      }
    >
      <Combobox
        items={[
          // A carrier can be cleared, listed first: "" is no contact's id.
          ...(of === "carriers" && value !== null
            ? [{ value: "", label: t("ui:clear"), icon: X }]
            : []),
          ...(contacts.data ?? []).map((contact) => ({
            value: String(contact.id),
            label: contact.name,
          })),
        ]}
        value={value === null ? null : String(value)}
        onValueChange={(next) => onChange(next ? Number(next) : null)}
        typedLabel={(typed) => (typed ? t(`${of}.add`, { name: typed }) : "")}
        onCreate={addContact}
        loading={contacts.isLoading}
        loadingLabel={t("hoard:common.loading")}
        placeholder={t(`${of}.choose`)}
        searchPlaceholder={t(`${of}.search`)}
        emptyMessage={t(`${of}.none`)}
      />
    </Field>
  );
}
