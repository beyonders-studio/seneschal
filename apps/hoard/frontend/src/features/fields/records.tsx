import { useQueryClient } from "@tanstack/react-query";
import { LayoutTemplate } from "lucide-react";
import { type ComponentType, type ReactNode, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import type {
  HoardDetailLayoutDefinitionOutput,
  HoardDetailLayoutRead,
  HoardLayoutSet,
  HoardListLayoutRead,
  HoardPropertyRead,
  HoardPropertySummary,
  RecordKind,
} from "@/api/generated/hoard.schemas";
import {
  getReadLayoutsQueryKey,
  resetLayout,
  saveLayout,
  useReadLayouts,
} from "@/api/generated/layouts/layouts";
import { listMemberNames, searchMemberNames } from "@/api/generated/members/members";
import {
  addPropertyOption,
  createProperty,
  getListPropertiesQueryKey,
  setPropertyValues,
  useListProperties,
} from "@/api/generated/properties/properties";
import { toast } from "@/components/Toaster";
import { errorMessage } from "@/lib/errors";
import { useIsAdmin, useSeesCosts } from "@/lib/permissions";
import { useSession } from "@/session";
import { Button } from "@/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/ui/dialog";
import { type DetailKind, DetailLayoutView, type ItemHome } from "@/ui/layouts/DetailLayoutView";
import type { DetailLayoutSpec, StoredRegions } from "@/ui/layouts/detailLayout";
import { columnsOf } from "@/ui/layouts/draft";
import type { FieldSaveState, ItemEdit } from "@/ui/layouts/editing";
import { ReadField } from "@/ui/layouts/editing";
import type { ItemSaves, PropertyEnv } from "@/ui/layouts/fieldEditors";
import {
  type FieldDef,
  fieldRenderer,
  isEmptyValue,
  type LayoutEnv,
  propertyFields,
  withDisplays,
} from "@/ui/layouts/fields";
import { type EditableDetail, type EditableLists, LayoutEditor } from "@/ui/layouts/LayoutEditor";
import type { SaveLayouts } from "@/ui/layouts/layoutSet";
import type { LayoutNode, Parts } from "@/ui/layouts/tree";
import type { Member, MemberId } from "@/ui/member-picker";
import type { FileFieldFiles } from "@/ui/properties/FileFieldInput";
import { listedValue, type PropertyDefinition } from "@/ui/properties/propertyHelpers";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/ui/table";

/** Where the words of Hoard's fields are. */
const NAMESPACES = ["fields", "catalog", "hoard", "layouts"] as const;

/** A record that holds properties: a material, a product or a lot. */
export type HoardRecord = { id: number; properties: HoardPropertySummary[]; updated_at?: string };

/** One kind of record, as its table lists it (`R`) and its page shows it (`I`). */
export interface HoardKind<R extends HoardRecord, I extends R = R> {
  kind: RecordKind;
  /** Its own fields a table can show as columns, which its page shows too. */
  fields: FieldDef<R>[];
  /** Its own fields only its page shows. */
  pageFields: FieldDef<I>[];
  /** The columns a table shows as shipped. */
  columns: string[];
  /** Its fields of what goods cost and sell for, which only a member who
   *  sees costs is shown. */
  costs?: ReadonlySet<string>;
  /** Its page as shipped. */
  spec: DetailLayoutSpec;
  /** Its own parts on its page, beside its fields. */
  parts?: Parts<I>;
  /** The page a record's name opens. */
  href: (item: R) => string;
}

/** Every field of a kind's page. */
const pageFields = <R extends HoardRecord, I extends R>(of: HoardKind<R, I>): FieldDef<I>[] => [
  ...of.fields,
  ...of.pageFields,
];

/** A field of a record's own, drawn by `Show`. */
export const builtin = <I,>(
  id: string,
  label: string,
  value: (item: I) => unknown,
  Show: ComponentType<{ item: I }>
): FieldDef<I> => ({
  id,
  label,
  source: "builtin",
  hideable: true,
  requireable: false,
  value,
  render: ({ item }) => <Show item={item} />,
});

/** A kind's fields by id: its own, its properties, then any `added`. */
const fieldMap = <I extends HoardRecord>(
  own: FieldDef<I>[],
  definitions: PropertyDefinition[],
  added: FieldDef[] = []
): Map<string, FieldDef<I>> =>
  new Map(
    [
      ...own,
      ...propertyFields<I>(definitions, (item, definition) =>
        listedValue(item.properties, definition)
      ),
      ...added,
    ].map((field) => [field.id, field])
  );

const NO_FILES: FileFieldFiles = {
  find: async () => [],
  href: () => "",
  preview: () => null,
  newFile: () => null,
};

const initials = (name: string) =>
  name
    .split(/\s+/)
    .slice(0, 2)
    .map((word) => word.charAt(0).toUpperCase())
    .join("") || "?";

/** What Hoard's property editors read and change: a kind's properties, made
 *  by an admin, and the members a person-valued property may name. */
export function usePropertyEnv(kind: RecordKind): PropertyEnv<HoardPropertyRead> {
  const { t } = useTranslation(["fields", "connection"]);
  const admin = useIsAdmin();
  const queryClient = useQueryClient();
  const definitions = useListProperties({ kind });
  return useMemo(() => {
    const refresh = () =>
      queryClient.invalidateQueries({ queryKey: getListPropertiesQueryKey({ kind }) });
    const told = (error: unknown): never => {
      toast.error(errorMessage(error, t, "saveFailed"));
      throw error;
    };
    return {
      definitions,
      createProperty: async ({ name, type, options, currency, counter }) => {
        if (!admin) {
          toast.error(t("adminOnly"));
          throw new Error("ADMIN_ONLY");
        }
        // The kit's types a plug-in's own fields take; none of them is offered.
        if (type === "link" || type === "badge" || type === "json") throw new Error(type);
        const made = await createProperty({ kind, name, type, options, currency, counter }).catch(
          told
        );
        await refresh();
        return made;
      },
      appendOption: async (definition, label) => {
        const option = await addPropertyOption(definition.id, { label }).catch(told);
        await refresh();
        return option;
      },
      // Hoard knows members by the references its records keep, and finds
      // them among the community's by their handle.
      searchMembers: async (_scope, query): Promise<Member<MemberId>[]> => {
        const named =
          "search" in query
            ? await searchMemberNames({ search: query.search })
            : query.ids.length
              ? await listMemberNames({ member_ref: query.ids.map(String) })
              : [];
        return named.map(({ member_ref, name }) => {
          const shown = name ?? t("connection:members.former");
          return { id: member_ref, name: shown, initials: initials(shown) };
        });
      },
      files: NO_FILES,
    };
  }, [kind, admin, definitions, queryClient, t]);
}

/** What a kind's fields are drawn with. */
function useLayoutEnv<I>(
  href: (item: I) => string,
  properties?: PropertyEnv<HoardPropertyRead>
): LayoutEnv<I> {
  const { t } = useTranslation(NAMESPACES);
  return useMemo(
    () => ({
      t: (key: string, options?: Record<string, unknown>) => t(key, options),
      communityPath: (path: string) => path,
      itemHref: href,
      properties,
    }),
    [t, href, properties]
  );
}

/** The kind's layouts as stored: its table's, and its page's as the kit
 *  draws a stored page. */
function useStoredLayouts(kind: RecordKind) {
  const { data } = useReadLayouts(kind);
  const table = data?.layouts.find((each): each is HoardListLayoutRead => each.kind === "table");
  const page = data?.layouts.find((each): each is HoardDetailLayoutRead => each.kind !== "table");
  return { set: data, table, page: page?.updated_at ? regionsOf(page.definition) : null };
}

/** A stored part, as a tree's node. */
const nodeOf = (
  part: NonNullable<HoardDetailLayoutDefinitionOutput["main"]>[number]
): LayoutNode => ({
  type: part.type,
  ...("props" in part && part.props ? { props: { ...part.props } } : {}),
  ...("children" in part && part.children ? { children: part.children.map(nodeOf) } : {}),
});

const regionsOf = (definition: HoardDetailLayoutDefinitionOutput): StoredRegions => ({
  header: definition.header?.map(nodeOf) ?? null,
  main: definition.main?.map(nodeOf) ?? null,
  side: definition.side?.map(nodeOf) ?? null,
});

/** The editor's tree as the regions a page draws. */
const treeRegions = (tree: LayoutNode): StoredRegions => ({
  header: tree.children?.[0]?.children ?? [],
  main: tree.children?.[1]?.children ?? [],
  side: tree.children?.[2]?.children ?? [],
});

/** The rows of a kind's table: each record, its `columns` in order. While
 *  the table is laid out, each column is marked for the editor. */
function Rows<I extends HoardRecord>({
  items,
  columns,
  fields,
  env,
  laidOut = false,
}: {
  items: I[];
  columns: string[];
  fields: ReadonlyMap<string, FieldDef<I>>;
  env: LayoutEnv<I>;
  laidOut?: boolean;
}) {
  const shown = columns.flatMap((id) => fields.get(id) ?? []);
  const mark = (field: FieldDef<I>, children: ReactNode) =>
    laidOut ? (
      <div className="contents" data-layout-node={`column:${field.id}`}>
        <div>{children}</div>
      </div>
    ) : (
      children
    );
  return (
    <Table>
      <TableHeader>
        <TableRow>
          {shown.map((field) => (
            <TableHead key={field.id}>
              {mark(field, field.source === "builtin" ? env.t(field.label) : field.label)}
            </TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => (
          <TableRow key={item.id}>
            {shown.map((field) => {
              const Draw = fieldRenderer(field);
              return (
                <TableCell key={field.id}>
                  {mark(
                    field,
                    <Draw
                      value={field.value(item)}
                      item={item}
                      field={field}
                      variant="cell"
                      env={env}
                    />
                  )}
                </TableCell>
              );
            })}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}

/** A kind's records in its table, its columns as its layout says. */
export function RecordTable<R extends HoardRecord, I extends R>({
  of,
  items,
}: {
  of: HoardKind<R, I>;
  items: R[];
}) {
  const definitions = useListProperties({ kind: of.kind });
  const env = useLayoutEnv(of.href);
  const { table } = useStoredLayouts(of.kind);
  const definition = table?.definition ?? {};
  const seesCosts = useSeesCosts();
  const fields = withDisplays(fieldMap(of.fields, definitions.data ?? []), definition.displays);
  const shape = { card: { type: "card" }, columns: of.columns, columnFields: new Set<string>() };
  return (
    <Rows
      items={items}
      columns={columnsOf(definition, fields, shape).filter((id) => seesCosts || !of.costs?.has(id))}
      fields={fields}
      env={env}
    />
  );
}

/** Where a record shown on its page lives: how its values are saved, whether
 *  they can be changed, and what can be done with it. */
type Home<I> = ItemHome<I, never>;

/** A kind's page, as the kit draws it. */
function detailOf<R extends HoardRecord, I extends R>(
  of: HoardKind<R, I>
): DetailKind<I, Home<I>, never, object, HoardPropertyRead> {
  const kind: DetailKind<I, Home<I>, never, object, HoardPropertyRead> = {
    spec: of.spec,
    fields: (definitions, added) => fieldMap(pageFields(of), definitions, added),
    namespaces: NAMESPACES,
    editors: {},
    parts: of.parts,
    home: (_item, home) => home,
    useExtension: () => {
      const properties = usePropertyEnv(of.kind);
      const env = useLayoutEnv<I>(of.href, properties);
      const seesCosts = useSeesCosts();
      return useMemo(
        () => ({
          env,
          fields: [],
          more: [],
          parts: {},
          // A field of the record's own that holds nothing, or a cost
          // without costs, is left out.
          field: (field: FieldDef<I>, item: I) => {
            if (isEmptyValue(field.value(item)) || (!seesCosts && of.costs?.has(field.id)))
              return null;
            const Draw = fieldRenderer(field);
            return (
              <ReadField label={env.t(field.label)}>
                <Draw
                  value={field.value(item)}
                  item={item}
                  field={field}
                  variant="detail"
                  env={env}
                />
              </ReadField>
            );
          },
          wrap: (_tree: LayoutNode, children: ReactNode) => children,
        }),
        [env, seesCosts]
      );
    },
  };
  return kind;
}

/** One property field's save of a record's values. */
function useValueSave<I>(kind: RecordKind, id: number, onSaved: () => void) {
  const { t } = useTranslation("fields");
  const [state, setState] = useState<FieldSaveState>("idle");
  const last = useRef<ItemEdit<I, never> | null>(null);
  const save = async (edit: ItemEdit<I, never>) => {
    if (!("properties" in edit)) return false;
    last.current = edit;
    setState("saving");
    try {
      await setPropertyValues(kind, id, edit.properties);
    } catch (error) {
      setState("error");
      toast.error(errorMessage(error, t, "saveFailed"));
      return false;
    }
    setState("saved");
    onSaved();
    return true;
  };
  return {
    state,
    save,
    retry: () => {
      if (last.current) void save(last.current);
    },
  };
}

/** How a record's values are saved, each change on its own. */
const savesOf = <I extends HoardRecord>(
  kind: RecordKind,
  item: I,
  onSaved: () => void
): ItemSaves<I, never> => ({
  useSave: () => useValueSave<I>(kind, item.id, onSaved),
  useShown: (properties) => properties,
});

/** A record's page: its fields, properties and parts where its layout puts
 *  them, its values changed in place while Hoard may change. */
export function RecordDetail<R extends HoardRecord, I extends R>({
  of,
  item,
  onSaved,
}: {
  of: HoardKind<R, I>;
  item: I;
  /** Told once a value is saved, to read the record again. */
  onSaved: () => void;
}) {
  const { read_only } = useSession();
  const detail = useMemo(() => detailOf(of), [of]);
  const saves = savesOf(of.kind, item, onSaved);
  const { page } = useStoredLayouts(of.kind);
  const home: Home<I> = { saves, readOnly: read_only, canOpen: { tool: of.kind, id: item.id } };
  return <DetailLayoutView detail={detail} item={item} context={home} layout={page} />;
}

/** For an admin, a button that opens the kit's layout editor on a kind's
 *  table, drawn with `rows`, and, given a record, on its page drawn with it. */
export function LayoutsButton<R extends HoardRecord, I extends R>({
  of,
  rows,
  page,
}: {
  of: HoardKind<R, I>;
  rows: R[];
  page?: I;
}) {
  const { t } = useTranslation("fields");
  const admin = useIsAdmin();
  const { read_only } = useSession();
  const [open, setOpen] = useState(false);
  if (!admin || read_only) return null;
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        <LayoutTemplate aria-hidden />
        {t("layouts")}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="flex h-[90vh] max-w-[95vw] flex-col gap-0 overflow-hidden p-0 sm:max-w-[95vw]">
          <DialogTitle className="sr-only">
            {t("layoutsTitle", { kind: t(`kinds.${of.kind}`) })}
          </DialogTitle>
          {open && <Editor of={of} rows={rows} page={page} onClose={() => setOpen(false)} />}
        </DialogContent>
      </Dialog>
    </>
  );
}

function Editor<R extends HoardRecord, I extends R>({
  of,
  rows,
  page: item,
  onClose,
}: {
  of: HoardKind<R, I>;
  rows: R[];
  page?: I;
  onClose: () => void;
}) {
  const { t } = useTranslation(NAMESPACES);
  const queryClient = useQueryClient();
  const propertyEnv = usePropertyEnv(of.kind);
  const env = useLayoutEnv(of.href, propertyEnv);
  const { set } = useStoredLayouts(of.kind);
  const detail = useMemo(() => detailOf(of), [of]);
  // A record's page drawn with nothing to change: the editor's preview.
  const home: Home<I> = {
    saves: {
      useSave: () => ({ state: "idle", save: async () => false, retry: () => {} }),
      useShown: (shown) => shown,
    },
    readOnly: true,
    canOpen: { tool: of.kind, id: item?.id ?? 0 },
  };
  if (!set) return null;

  const save: SaveLayouts = async (changes) => {
    let stored: HoardLayoutSet | undefined = set;
    try {
      for (const change of changes) {
        // A kind has one table, which it always opens on.
        if ("save" in change) stored = await saveLayout(of.kind, change.save);
        else if ("reset" in change) stored = await resetLayout(of.kind, change.reset);
      }
    } catch (error) {
      toast.error(errorMessage(error, t, "fields:saveFailed"));
      throw error;
    } finally {
      await queryClient.invalidateQueries({ queryKey: getReadLayoutsQueryKey(of.kind) });
    }
    return stored;
  };
  const lists: EditableLists<HoardPropertyRead> = {
    fields: (definitions, plugins) => fieldMap(of.fields, definitions, plugins),
    shape: {
      card: { type: "card" },
      columns: of.columns,
      columnFields: new Set(of.fields.map((field) => field.id)),
    },
    preview: ({ layout, fields, frame }) =>
      frame(
        <Rows
          items={rows}
          columns={columnsOf(layout.definition, fields, layout.shape)}
          fields={withDisplays(fields, layout.definition.displays)}
          env={env}
          laidOut
        />
      ),
    help: () => "fields:tableHelp",
  };
  const pageLayout: EditableDetail<HoardPropertyRead> = {
    spec: of.spec,
    words: {
      name: `fields:pages.${of.kind}`,
      help: "fields:pageHelp",
      shipped: "fields:shipped",
      titleHelp: "fields:titleHelp",
      hiddenHelp: "fields:hiddenHelp",
      parts: "fields:",
    },
    fields: (definitions, plugins) => fieldMap(pageFields(of), definitions, plugins),
    preview: (tree) =>
      item ? (
        <DetailLayoutView
          detail={detail}
          item={item}
          context={home}
          layout={treeRegions(tree)}
          editing
        />
      ) : null,
  };
  return (
    <LayoutEditor
      t={env.t}
      lists={lists}
      details={item ? [pageLayout] : []}
      propertyEnv={propertyEnv}
      layouts={set}
      save={save}
      initialKind={item ? of.kind : "table"}
      metadataHref="/settings#settings-properties"
      onClose={onClose}
    />
  );
}
