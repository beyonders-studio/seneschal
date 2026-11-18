"""The importers Hoard registers with Seneschal's imports
(``seneschal_core.imports``): one per program whose exports it reads."""

from __future__ import annotations

from hoard.imports import stocksmith
from seneschal_core.imports import Importer

IMPORTERS: tuple[Importer, ...] = (stocksmith.IMPORTER,)
