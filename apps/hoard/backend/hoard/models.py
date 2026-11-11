"""Every table Hoard's database holds, for Alembic to compare against."""

from sqlmodel import SQLModel

from seneschal_core.auth import HandoffRedemption
from seneschal_core.organizations import Organization

metadata = SQLModel.metadata

__all__ = ["HandoffRedemption", "Organization", "metadata"]
