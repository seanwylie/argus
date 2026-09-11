"""Filesystem layout under each product (layered API)."""

from __future__ import annotations

from argus.adapters.base import AdapterCategory
from argus.adapters.builtins._wrap import SignalAdapterWrapper
from argus.signals.adapters.filesystem import FilesystemAdapter


class FilesystemIntegrationAdapter(SignalAdapterWrapper):
    """Delegates to :class:`~argus.signals.adapters.filesystem.FilesystemAdapter`."""

    def __init__(self) -> None:
        super().__init__(FilesystemAdapter())

    @property
    def category(self) -> AdapterCategory:
        return AdapterCategory.FILESYSTEM
