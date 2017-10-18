import errno
import logging
import os
import subprocess
import sys

import gi
gi.require_version('OSTree', '1.0')
from gi.repository import GLib, Gio, OSTree

from ConfigParser import ConfigParser


OSTREE_COMMIT_GVARIANT_STRING = "(a{sv}aya(say)sstayay)"

OSTREE_STATIC_DELTA_META_ENTRY_FORMAT = "(uayttay)"

OSTREE_STATIC_DELTA_FALLBACK_FORMAT = "(yaytt)"

OSTREE_STATIC_DELTA_SUPERBLOCK_FORMAT = \
    "(a{sv}tayay" + OSTREE_COMMIT_GVARIANT_STRING + \
    "aya" + OSTREE_STATIC_DELTA_META_ENTRY_FORMAT + \
    "a" + OSTREE_STATIC_DELTA_FALLBACK_FORMAT + \
    ")"


class FlatpakImporter():
    CONFIG_PATHS = [ '/etc/ostree/flatpak-import.conf',
                     os.path.expanduser('~/.config/ostree/flatpak-import.conf'),
                    'flatpak-import.conf' ]

    METADATA_KEYS = [ 'ref',
                      'flatpak',
                      'origin',
                      'runtime-repo',
                      'metadata',
                      'gpg-keys' ]

    @staticmethod
    def _parse_config():
        config = ConfigParser()
        config.read(FlatpakImporter.CONFIG_PATHS)

        configs = {}
        if config.has_section('defaults'):
            configs = dict(config.items('defaults'))

        return configs

    @staticmethod
    def _get_metadata_contents(repo, rev):
        """Read the contents of the commit's metadata file"""
        _, root, checksum = repo.read_commit(rev)
        logging.debug("Commit_checksum: " + checksum)

        # Get the file and size
        metadata_file = Gio.File.resolve_relative_path (root, 'metadata');
        metadata_info = metadata_file.query_info(
            Gio.FILE_ATTRIBUTE_STANDARD_SIZE, Gio.FileQueryInfoFlags.NONE)
        metadata_size = metadata_info.get_attribute_uint64(
            Gio.FILE_ATTRIBUTE_STANDARD_SIZE)

        logging.debug("Metadata file size: {}".format(str(metadata_size)))

        # Open it for reading and return the data
        metadata_stream = metadata_file.read()
        metadata_bytes = metadata_stream.read_bytes(metadata_size)

        return metadata_bytes.get_data()

    @staticmethod
    def import_flatpak(flatpak,
                       repository,
                       gpg_homedir = None,
                       keyring = None,
                       sign_key = None):

        defaults = FlatpakImporter._parse_config()

        # If we only have defaults, use those instead
        gpg_homedir = gpg_homedir or defaults.get('gpg_homedir', None)
        keyring = keyring or defaults.get('keyring', None)
        sign_key = sign_key or defaults.get('sign_key', None)

        logging.info("Starting the Flatpak import process...")
        for arg in ['flatpak', 'repository', 'gpg_homedir', 'keyring', 'sign_key']:
            logging.info("Set {} to {}".format(arg, locals()[arg]))

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
        logging.debug("Metadata keys: {0}".format(metadata_variant.keys()))

        commit = OSTree.checksum_from_bytes_v(checksum_variant)

        metadata = {}
        for key in FlatpakImporter.METADATA_KEYS:
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
            try:
                os.makedirs(repository)
            except OSError as err:
                if err.errno != errno.EEXIST:
                    raise
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
            keyring_dir = None
            if gpg_homedir:
                keyring_dir = Gio.File.new_for_path(gpg_homedir)

            trusted_keyring = None
            if keyring:
                trusted_keyring = Gio.File.new_for_path(keyring)

            gpg_verify_result = repo.verify_commit_ext(commit,
                                                       keyringdir=keyring_dir,
                                                       extra_keyring=trusted_keyring,
                                                       cancellable=None)
            # TODO: Handle signature requirements
            if gpg_verify_result and gpg_verify_result.count_valid() > 0:
                logging.info("Valid flatpak signature found")
            else:
                raise Exception("Flatpak does not have valid signature!")

            # Compare installed and header metadata, remove commit if mismatch
            metadata_contents = FlatpakImporter._get_metadata_contents(repo, commit)
            if metadata_contents == metadata['metadata']:
                logging.debug("Committed metadata matches the static delta header")
            else:
                raise Exception("Committed metadata does not match the static delta header")

            # Sign the commit
            if sign_key:
                logging.info("Signing with key {} from {}".format(sign_key,
                                                                  keyring_dir))
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

        # Update the repo metadata (summary, appstream, etc), but no
        # pruning or delta generation to make it fast
        cmd = ['flatpak', 'build-update-repo']
        if gpg_homedir:
            cmd.append('--gpg-homedir=' + gpg_homedir)
        if sign_key:
            cmd.append('--gpg-sign=' + sign_key)
        cmd.append(repository)

        logging.info('Updating repository metadata')
        logging.debug('Executing ' + ' '.join(cmd))

        subprocess.check_call(cmd)
