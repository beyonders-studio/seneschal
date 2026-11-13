import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";

import type { SettingsLocationRead } from "@/api/generated/hoard.schemas";
import {
  createSettingsLocation,
  deleteSettingsLocation,
  getListSettingsLocationsQueryKey,
  updateSettingsLocation,
  useListSettingsLocations,
} from "@/api/generated/settings/settings";
import { toast } from "@/components/Toaster";
import { ContactPicker, useContactNames } from "@/features/contacts/ContactPicker";
import { NamedList, useChangesSettings } from "@/features/settings/NamedList";
import { useLocationName } from "@/features/settings/names";
import { SettingsSection } from "@/features/settings/SettingsSection";
import { errorMessage } from "@/lib/errors";

/** The places stock is kept: the main location, which stays, and the rest,
 *  each with the contact holding its address. */
export function LocationsSection() {
  const { t } = useTranslation("catalog");
  const nameOf = useLocationName();
  return (
    <SettingsSection id="locations" title={t("settings.locations.title")}>
      <NamedList
        words="locations"
        items={useListSettingsLocations().data}
        queryKey={getListSettingsLocationsQueryKey()}
        nameOf={nameOf}
        add={(name) => createSettingsLocation({ name })}
        rename={(location, name) => updateSettingsLocation(location.id, { name })}
        remove={(location) => deleteSettingsLocation(location.id)}
        removable={(location) => !location.main}
        detail={(location) => <LocationContact location={location} name={nameOf(location)} />}
      />
    </SettingsSection>
  );
}

/** The contact holding a location's address and phone, which an admin picks
 *  among the community's contacts; everyone else reads its name. */
function LocationContact({ location, name }: { location: SettingsLocationRead; name: string }) {
  const { t } = useTranslation("catalog");
  const queries = useQueryClient();
  const changes = useChangesSettings();
  const contactName = useContactNames();
  const save = useMutation({
    mutationFn: (contactId: number | null) =>
      updateSettingsLocation(location.id, { name: location.name, contact_id: contactId }),
    onSuccess: () => queries.invalidateQueries({ queryKey: getListSettingsLocationsQueryKey() }),
    onError: (error) => toast.error(errorMessage(error, t, "settings.saveFailed")),
  });
  if (!changes)
    return <span className="text-muted-foreground">{contactName(location.contact_id)}</span>;
  return (
    <ContactPicker
      of="locations"
      label={t("settings.locations.contact", { name })}
      value={location.contact_id}
      onChange={(contactId) => save.mutate(contactId)}
    />
  );
}
