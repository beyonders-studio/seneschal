import { createFileRoute, notFound } from "@tanstack/react-router";

import { ImportPage, isImporter } from "@/features/imports/ImportPage";

/** Settings › Import, by importer, for the community's admins. */
export const Route = createFileRoute("/settings_/import/$importer")({
  beforeLoad: ({ params }) => {
    if (!isImporter(params.importer)) throw notFound();
  },
  component: Page,
});

function Page() {
  const { importer } = Route.useParams();
  return isImporter(importer) ? <ImportPage importer={importer} /> : null;
}
