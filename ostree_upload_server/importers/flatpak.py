import logging

import gi

from .base import BaseImporter
from .util import get_metadata_contents

gi.require_version('OSTree', '1.0')
from gi.repository import GLib, OSTree  # noqa: E402

OSTREE_COMMIT_GVARIANT_STRING = "(a{sv}aya(say)sstayay)"

OSTREE_STATIC_DELTA_META_ENTRY_FORMAT = "(uayttay)"

OSTREE_STATIC_DELTA_FALLBACK_FORMAT = "(yaytt)"

OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT = \
    "(a{sv}tayay" + OSTREE_COMMIT_GVARIANT_STRING + \
    "aya" + OSTREE_STATIC_DELTA_META_ENTRY_FORMAT + \
    "a" + OSTREE_STATIC_DELTA_FALLBACK_FORMAT + \
    ")"


class FlatpakImporter(BaseImporter):
    MIME_TYPE = 'application/octet-stream'

    METADATA_KEYS = ['ref',
                     'flatpak',
                     'origin',
                     'runtime-repo',
                     'metadata',
                     'gpg-keys']

    def _import_commit(self, commit, src_path_obj, target_repo):
        # Apply the delta to the target repo
        target_repo.static_delta_execute_offline(src_path_obj, False, None)

        # Compare installed and header metadata, remove commit if
        # mismatch (abort transaction)
        metadata_contents = get_metadata_contents(target_repo, commit)
        if metadata_contents != self._metadata['metadata']:
            raise Exception("Committed metadata does not match the static "
                            "delta header")

        logging.debug("Committed metadata matches the static delta header")

    def import_to_repo(self):
        logging.info("Trying to use %s extractor...", self.__class__.__name__)

        # Mmap the flatpak file and create a GLib.Variant from it
        mapped_file = GLib.MappedFile.new(self._src_path, False)
        delta = GLib.Variant.new_from_bytes(
            GLib.VariantType(OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT),
            mapped_file.get_bytes(),
            False)

        # Parse flatpak metadata
        # Use get_child_value instead of array index to avoid
        # slowdown (constructing the whole array?)
        checksum_variant = delta.get_child_value(3)
        OSTree.validate_structureof_csum_v(checksum_variant)

        metadata_variant = delta.get_child_value(0)
        logging.debug("Metadata keys: %s", list(metadata_variant.keys()))

        commit = OSTree.checksum_from_bytes_v(checksum_variant)

        self._metadata = {}
        logging.debug("===== Start Metadata =====")
        for key in FlatpakImporter.METADATA_KEYS:
            try:
                self._metadata[key] = metadata_variant[key]
            except KeyError:
                self._metadata[key] = ''

            logging.debug(" %s: %s", key, self._metadata[key])
        logging.debug("===== End Metadata =====")

        self._apply_commit_to_repo(commit, self._metadata['ref'])
