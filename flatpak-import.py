#!/usr/bin/env python2

from argparse import ArgumentParser
from ConfigParser import ConfigParser
import logging
import os
import sys

import gi
gi.require_version('OSTree', '1.0')
from gi.repository import GLib, Gio, OSTree


OSTREE_COMMIT_GVARIANT_STRING = "(a{sv}aya(say)sstayay)"

OSTREE_STATIC_DELTA_META_ENTRY_FORMAT = "(uayttay)"

OSTREE_STATIC_DELTA_FALLBACK_FORMAT = "(yaytt)"

OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT = \
    "(a{sv}tayay" + OSTREE_COMMIT_GVARIANT_STRING + \
    "aya" + OSTREE_STATIC_DELTA_META_ENTRY_FORMAT + \
    "a" + OSTREE_STATIC_DELTA_FALLBACK_FORMAT + \
    ")"

def _parse_args_and_config():
    aparser = ArgumentParser(
        description='Import flatpak into a local repository ',
    )
    aparser.add_argument('repo',
                         help='repository name to use')
    aparser.add_argument('flatpak', help='file to import')
    aparser.add_argument('-g', '--gpg-homedir',
                         help='GPG homedir to use when looking for keyrings')
    aparser.add_argument('-k', '--keyring', help='additional trusted keyring file')
    aparser.add_argument('-s', '--sign-key', help='GPG key ID to sign the commit with')
    aparser.add_argument('-v', '--verbose', action='store_true',
                         help='verbose log output')
    aparser.add_argument('-d', '--debug', action='store_true',
                         help='debug log output')

    config = ConfigParser()
    config.read([
        '/etc/ostree/flatpak-import.conf',
        os.path.expanduser('~/.config/ostree/flatpak-import.conf'),
        'flatpak-import.conf'
    ])
    if config.has_section('defaults'):
        aparser.set_defaults(**dict(config.items('defaults')))

    return aparser.parse_args()


def _get_metadata_contents(repo, rev):
    """Read the contents of the commit's metadata file"""
    _, root, checksum = repo.read_commit(rev)
    logging.debug("commit_checksum: " + checksum)

    # Get the file and size
    metadata_file = Gio.File.resolve_relative_path (root, 'metadata');
    metadata_info = metadata_file.query_info(
        Gio.FILE_ATTRIBUTE_STANDARD_SIZE, Gio.FileQueryInfoFlags.NONE)
    metadata_size = metadata_info.get_attribute_uint64(
        Gio.FILE_ATTRIBUTE_STANDARD_SIZE)
    logging.debug("metadata file size:" + str(metadata_size))

    # Open it for reading and return the data
    metadata_stream = metadata_file.read()
    metadata_bytes = metadata_stream.read_bytes(metadata_size)

    return metadata_bytes.get_data()


def import_flatpak(flatpak,
                   repository,
                   gpg_homedir,
                   keyring,
                   sign_key):
    ### Mmap the flatpak file and create a GLib.Variant from it
    mapped_file = GLib.MappedFile.new(flatpak, False)
    mapped_bytes = mapped_file.get_bytes()
    ostree_static_delta_superblock_format = GLib.VariantType(
            OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT)
    delta = GLib.Variant.new_from_bytes(
            ostree_static_delta_superblock_format,
            mapped_bytes,
            False)

    ### Parse flatpak metadata
    # Use get_child_value instead of array index to avoid
    # slowdown (constructing the whole array?)
    checksum_variant = delta.get_child_value(3)
    OSTree.validate_structureof_csum_v(checksum_variant)

    metadata_variant = delta.get_child_value(0)
    logging.debug("metadata keys: {0}".format(metadata_variant.keys()))

    commit = OSTree.checksum_from_bytes_v(checksum_variant)

    metadata = {}
    metadata_keys = [ 'ref',
            'flatpak',
            'origin',
            'runtime-repo',
            'metadata',
            'gpg-keys' ]

    for key in metadata_keys:
        try:
            metadata[key] = metadata_variant[key]
        except KeyError:
            metadata[key] = ''
        logging.debug("{0}: {1}".format(key, metadata[key]))

    # Open repository
    repo_file = Gio.File.new_for_path(repository)
    repo = OSTree.Repo(path=repo_file)
    if os.path.exists(os.path.join(repository, 'config')):
        logging.info('Opening repo at ' + repository)
        repo.open()
    else:
        logging.info('Creating archive-z2 repo at ' + repository)
        os.makedirs(repository)
        repo.create(OSTree.RepoMode.ARCHIVE_Z2)

    # See if the ref is already pointed at this commit
    _, current_rev = repo.resolve_rev(metadata['ref'], allow_noent=True)
    if current_rev == commit:
        logging.info('Ref {} already at commit {}'
                     .format(metadata['ref'], commit))
        return

    try:
        # Prepare transaction
        repo.prepare_transaction(None)
        repo.transaction_set_ref(None, metadata['ref'], commit)

        # Execute the delta
        flatpak_file = Gio.File.new_for_path(flatpak)
        repo.static_delta_execute_offline(flatpak_file, False, None)

        # Verify gpg signature
        if gpg_homedir:
            gpg_homedir = Gio.File.new_for_path(gpg_homedir)
        else:
            gpg_homedir = None
        if keyring:
            trusted_keyring = Gio.File.new_for_path(keyring)
        else:
            trusted_keyring = None
        gpg_verify_result = repo.verify_commit_ext(commit,
                                                   keyringdir=gpg_homedir,
                                                   extra_keyring=trusted_keyring,
                                                   cancellable=None)
        # TODO: Handle signature requirements
        if gpg_verify_result and gpg_verify_result.count_valid() > 0:
            logging.info("valid signature found")
        else:
            raise Exception("no valid signature")

        # Compare installed and header metadata, remove commit if mismatch
        metadata_contents = _get_metadata_contents(repo, commit)
        if metadata_contents == metadata['metadata']:
            logging.debug("committed metadata matches the static delta header")
        else:
            raise Exception("committed metadata does not match the static delta header")

        # Sign the commit
        if sign_key:
            logging.debug("should sign with key " + sign_key)
            try:
                repo.sign_commit(commit_checksum=commit,
                                 key_id=sign_key,
                                 homedir=gpg_homedir,
                                 cancellable=None)
            except GLib.Error as err:
                if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                    # Already signed with this key
                    logging.debug("already signed with key " + sign_key)
                else:
                    raise

        # Commit the transaction
        repo.commit_transaction(None)

    except:
        repo.abort_transaction(None)
        raise


if __name__ == "__main__":
    args = _parse_args_and_config()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    import_flatpak(args.flatpak,
                   args.repo,
                   args.gpg_homedir,
                   args.keyring,
                   args.sign_key)
