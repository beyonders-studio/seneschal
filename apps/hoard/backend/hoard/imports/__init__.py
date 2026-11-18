"""The importers Hoard registers with Seneschal's imports
(``seneschal_core.imports``): one per program whose exports it reads, and
one for Hoard's own catalogue spreadsheets."""

from __future__ import annotations

from hoard.imports import spreadsheets, stocksmith
from seneschal_core.imports import Importer

IMPORTERS: tuple[Importer, ...] = (stocksmith.IMPORTER, spreadsheets.IMPORTER)
