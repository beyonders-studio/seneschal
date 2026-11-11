# Seneschal

Business apps for [Initiative](https://github.com/beyonders-studio/initiative).
Each app is a plug-in a community installs, with pages of its own inside
Initiative and a database of its own.

- **Hoard**: inventory and production. Materials, products, recipes,
  production runs and their lots, purchases from suppliers, and stock kept in
  step with an online store. Early: being built now.

## Layout

```
apps/hoard/backend    the Hoard service (FastAPI, Alembic)
apps/hoard/frontend   Hoard's pages (Vite, React)
packages/core         what every app's backend shares
packages/ui           Initiative's UI kit, copied whole
scripts/ci/check      the checks CI runs
```

## Licence

GNU Affero General Public License v3.0, as Initiative is. We aren't taking
outside contributions yet.
