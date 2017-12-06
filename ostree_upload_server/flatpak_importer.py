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

# Indices into the commit gvariant tuple
COMMIT_SUBJECT_INDEX = 3
COMMIT_BODY_INDEX = 4
COMMIT_TREE_CONTENT_CHECKSUM_INDEX = 6
COMMIT_TREE_METADATA_CHECKSUM_INDEX = 7

# OSTree on SOMA does not have experimental API, so add some
# compatibility settings
#
# FIXME: Remove this when P2P bindings are no longer experimental and
# can then be expected in SOMA
if not hasattr(OSTree, 'COMMIT_META_KEY_COLLECTION_BINDING'):
    OSTree.COMMIT_META_KEY_COLLECTION_BINDING = 'ostree.collection-binding'
if not hasattr(OSTree, 'COMMIT_META_KEY_REF_BINDING'):
    OSTree.COMMIT_META_KEY_REF_BINDING = 'ostree.ref-binding'


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

    # FIXME: Remove this when P2P bindings are no longer experimental
    # and can then be expected in SOMA
    @staticmethod
    def _get_collection_id(repo):
        """Compatibility wrapper for OSTree.Repo.get_collection_id"""
        if hasattr(repo, 'get_collection_id'):
            return repo.get_collection_id()

        # Emulate it by seeing if core.collection-id is set. GKeyFile
        # doesn't have any means to check if a key exists, so you have
        # to catch errors.
        config = repo.get_config()
        try:
            collection_id = config.get_string('core', 'collection-id')
        except GLib.Error as err:
            if err.matches(GLib.key_file_error_quark(),
                           GLib.KeyFileError.KEY_NOT_FOUND):
                collection_id = None
            else:
                raise

        return collection_id

    @staticmethod
    def _copy_commit(repo, src_rev, dest_ref):
        """Copy commit src_rev to dest_ref

        This makes the new commit at dest_ref have the proper collection
        binding for this repo. The caller is expected to manage the
        ostree transaction.

        This is like "flatpak build-commit-from", but we need more
        control over the transaction.
        """
        logging.info('Copying commit %s to %s', src_rev, dest_ref)

        _, src_root, _ = repo.read_commit(src_rev)
        _, src_variant, src_state = repo.load_commit(src_rev)

        # Only copy normal commits
        if src_state != 0:
            raise Exception('Cannot copy irregular commit {}'
                            .format(src_rev))

        # If the dest ref exists, use the current commit as the new
        # commit's parent
        _, dest_parent = repo.resolve_rev_ext(
            dest_ref, allow_noent=True,
            flags=OSTree.RepoResolveRevExtFlags.REPO_RESOLVE_REV_EXT_NONE)
        if dest_parent is not None:
            logging.info('Using %s as new commit parent', dest_parent)

        # Make a copy of the commit metadata to update. Like flatpak
        # build-commit-from, the detached metadata is not copied since
        # the only known usage is for GPG signatures, which would become
        # invalid.
        commit_metadata = GLib.VariantDict.new(src_variant.get_child_value(0))

        # Set the collection binding if the repo has a collection ID,
        # otherwise remove it
        collection_id = FlatpakImporter._get_collection_id(repo)
        if collection_id is not None:
            commit_metadata.insert_value(
                OSTree.COMMIT_META_KEY_COLLECTION_BINDING,
                GLib.Variant('s', collection_id))
        else:
            commit_metadata.remove(
                OSTree.COMMIT_META_KEY_COLLECTION_BINDING)

        # Include the destination ref in the ref bindings
        ref_bindings = commit_metadata.lookup_value(
            OSTree.COMMIT_META_KEY_REF_BINDING,
            GLib.VariantType('as'))
        if ref_bindings is None:
            ref_bindings = []
        ref_bindings = set(ref_bindings)
        ref_bindings.add(dest_ref)
        commit_metadata.insert_value(
            OSTree.COMMIT_META_KEY_REF_BINDING,
            GLib.Variant('as', sorted(ref_bindings)))

        # Add flatpak specific metadata. xa.ref is deprecated, but some
        # flatpak clients might expect it. xa.from_commit will be used
        # by the app verifier to make sure the commit it sent actually
        # got there
        commit_metadata.insert_value('xa.ref',
                                     GLib.Variant('s', dest_ref))
        commit_metadata.insert_value('xa.from_commit',
                                     GLib.Variant('s', src_rev))

        # Convert from GVariantDict to GVariant vardict
        commit_metadata = commit_metadata.end()

        # Copy other commit data from source commit
        commit_subject = src_variant[COMMIT_SUBJECT_INDEX]
        commit_body = src_variant[COMMIT_BODY_INDEX]
        commit_time = OSTree.commit_get_timestamp(src_variant)

        # Make the new commit assuming the caller started a transaction
        mtree = OSTree.MutableTree.new()
        repo.write_directory_to_mtree(src_root, mtree, None)
        _, dest_root = repo.write_mtree(mtree)
        _, dest_checksum = repo.write_commit_with_time(dest_parent,
                                                       commit_subject,
                                                       commit_body,
                                                       commit_metadata,
                                                       dest_root,
                                                       commit_time)
        logging.info('Created new commit %s', dest_checksum)

        return dest_checksum

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
        logging.debug('Current {} commit: {}'.format(metadata['ref'],
                                                     current_rev))
        if current_rev == commit:
            logging.info('Ref {} already at commit {}'
                         .format(metadata['ref'], commit))
            return

        try:
            # Prepare transaction
            repo.prepare_transaction(None)

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

            # Copy the commit to get correct collection and ref bindings
            new_commit = FlatpakImporter._copy_commit(repo, commit,
                                                      metadata['ref'])

            # Sign the commit
            if sign_key:
                logging.info("Signing with key {} from {}"
                             .format(sign_key, gpg_homedir))
                try:
                    repo.sign_commit(commit_checksum=new_commit,
                                     key_id=sign_key,
                                     homedir=gpg_homedir,
                                     cancellable=None)
                except GLib.Error as err:
                    if err.matches(Gio.io_error_quark(), Gio.IOErrorEnum.EXISTS):
                        # Already signed with this key
                        logging.debug("already signed with key " + sign_key)
                    else:
                        raise

            # Set the ref to the new commit. Ideally this would use
            # transaction_set_collection_ref, but that's not available
            # on SOMA and the commit was set to use this repo's
            # collection ID, so it wouldn't make any difference.
            repo.transaction_set_ref(None, metadata['ref'], new_commit)

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
