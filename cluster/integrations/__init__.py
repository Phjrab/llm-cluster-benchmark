"""Compatibility integration boundaries used during incremental migration."""

from .legacy_inventory import LegacyInventoryConversion, LegacyNodeRecord, adapt_legacy_inventory

__all__ = ["LegacyInventoryConversion", "LegacyNodeRecord", "adapt_legacy_inventory"]
