#!/usr/bin/env python2

from argparse import ArgumentParser
import gi
gi.require_version('OSTree', '1.0')
from gi.repository import GLib, Gio, OSTree
import logging
import os


OSTREE_COMMIT_GVARIANT_STRING = "(a{sv}aya(say)sstayay)"

OSTREE_STATIC_DELTA_META_ENTRY_FORMAT = "(uayttay)"

OSTREE_STATIC_DELTA_FALLBACK_FORMAT = "(yaytt)"

OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT = \
    "(a{sv}tayay" + OSTREE_COMMIT_GVARIANT_STRING + \
    "aya" + OSTREE_STATIC_DELTA_META_ENTRY_FORMAT + \
    "a" + OSTREE_STATIC_DELTA_FALLBACK_FORMAT + \
    ")"


if __name__ == "__main__":

    aparser = ArgumentParser(
        description='Import flatpak into a local repository ',
    )
    aparser.add_argument('repo',
                         help='repository name to use')
    aparser.add_argument('flatpak', help='file to import')
    aparser.add_argument('-v', '--verbose', action='store_true',
                         help='verbose log output')
    aparser.add_argument('-d', '--debug', action='store_true',
                         help='debug log output')
    args = aparser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)


    ### mmap the flatpak file and create a GLib.Variant from it

    mapped_file = GLib.MappedFile.new(args.flatpak, False)
    mapped_bytes = mapped_file.get_bytes()
    ostree_static_delta_superblock_format = GLib.VariantType(
            OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT)
    delta = GLib.Variant.new_from_bytes(
            ostree_static_delta_superblock_format,
            mapped_bytes,
            False)


    ### parse flatpak metadata

    checksum_variant = delta.get_child_value(3)
    if not OSTree.validate_structureof_csum_v(checksum_variant):
        # checksum format invalid
        pass

    metadata_variant = delta[0]

    commit = OSTree.checksum_from_bytes_v(checksum_variant)

    ref = metadata_variant['ref']

    if 'flatpak' in metadata_variant.keys():
        flatpak = metadata_variant['flatpak']
    else:
        flatpak = ''

    if 'origin' in metadata_variant.keys():
        origin = metadata_variant['origin']
    else:
        origin = ''

    if 'runtime-repo' in metadata_variant.keys():
        runtime_repo = metadata_variant['runtime-repo']
    else:
        runtime_repo = ''

    if 'metadata' in metadata_variant.keys():
        app_metadata = metadata_variant['metadata']
    else:
        app_metadata = ''

    if 'gpg-keys' in metadata_variant.keys():
        gpg_keys = metadata_variant['gpg-keys']
    else:
        gpg_keys = ''

    logging.debug("commit: " + commit)
    logging.debug("flatpak: " + commit)
    logging.debug("ref: " + ref)
    logging.debug("origin: " + origin)
    logging.debug("runtime_repo: " + runtime_repo)
    logging.debug("app_metadata:\n---\n" + app_metadata + "---")
    logging.debug("gpg_keys: " + gpg_keys)

    logging.debug("metadata keys: " + str(metadata_variant.keys()))


    # open repository

    repo_file = Gio.File.new_for_path(args.repo)
    repo = OSTree.Repo(path=repo_file)
    if os.path.exists(os.path.join(args.repo, 'config')):
        logging.info('Opening repo at ' + args.repo)
        repo.open()
    else:
        logging.info('Creating archive-z2 repo at ' + args.repo)
        os.makedirs(args.repo)
        repo.create(OSTree.RepoMode.ARCHIVE_Z2)


    # prepare transaction

    repo.prepare_transaction(None)
    repo.transaction_set_ref(None, ref, commit)


    # execute the delta

    flatpak_file = Gio.File.new_for_path(args.flatpak)
    repo.static_delta_execute_offline(flatpak_file, False, None)


    # verify gpg signature


    # commit the transaction

    repo.commit_transaction(None)


    # grab commit root

    (ret, commit_root, commit_checksum) = repo.read_commit(ref, None)
    if not ret:
        logging.critical("commit failed")
    else:
        logging.debug("commit_checksum: " + commit_checksum)


    # compare installed and header metadata, remove commit if mismatch


    # sign the commit



