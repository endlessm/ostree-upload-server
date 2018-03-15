import errno
import logging
import os
import subprocess

import gi
gi.require_version('OSTree', '1.0')
from gi.repository import GLib, Gio, OSTree
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


# FIXME: Remove this when P2P bindings are no longer experimental
# and can then be expected in SOMA
def get_collection_id(repo):
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

def copy_commit(repo, src_rev, dest_ref):
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
    collection_id = get_collection_id(repo)
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

def open_repository(repo_path):
    # Open repository
    repo_file = Gio.File.new_for_path(repo_path)
    repo = OSTree.Repo(path=repo_file)

    if os.path.exists(os.path.join(repo_path, 'config')):
        logging.info('Opening repo at %s', repo_path)
        repo.open()
    else:
        logging.info('Creating archive-z2 repo at %s', repo_path)
        try:
            os.makedirs(repo_path)
        except OSError as err:
            if err.errno != errno.EEXIST:
                raise
        repo.create(OSTree.RepoMode.ARCHIVE_Z2)

    return repo

def verify_commit_sig(repo, commit, gpg_homedir, keyring):
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
        raise Exception("Bundle does not have valid signature!")

# Update the repo metadata (summary, appstream, etc), but no
# pruning or delta generation to make it fast
def update_repo_metadata(self):
    cmd = ['flatpak', 'build-update-repo']
    if self._gpg_homedir:
        cmd.append('--gpg-homedir={}'.format(self._gpg_homedir))
    if self._sign_key:
        cmd.append('--gpg-sign={}'.format(self._sign_key))
    cmd.append(self._repository_path)

    logging.info('Updating repository metadata')
    logging.debug('Executing %s', ' '.join(cmd))

    subprocess.check_call(cmd)

def find_repo(start_path):
    refs_suffix = os.path.join('refs', 'heads')

    toplevel_refs_dir = os.path.join(start_path, refs_suffix)
    if os.path.isdir(toplevel_refs_dir):
        logging.debug("Found refs in toplevel dir!")
        return start_path

    # Pretty basic but we assume it's the first item (directory) in the dirlist
    first_level_path = os.listdir(start_path)[0]
    nested_refs_dir = os.path.join(start_path, first_level_path, refs_suffix)
    if os.path.isdir(nested_refs_dir):
        logging.debug("Found refs in first child dir!")
        return os.path.join(start_path, first_level_path)

    raise RuntimeError("Repo did not have the expected layout!")

def get_metadata_contents(repo, rev):
    """Read the contents of the commit's metadata file"""
    _, root, checksum = repo.read_commit(rev)
    logging.debug("Commit_checksum: %s", checksum)

    # Get the file and size
    metadata_file = Gio.File.resolve_relative_path(root, 'metadata')
    metadata_info = metadata_file.query_info(
        Gio.FILE_ATTRIBUTE_STANDARD_SIZE, Gio.FileQueryInfoFlags.NONE)
    metadata_size = metadata_info.get_attribute_uint64(
        Gio.FILE_ATTRIBUTE_STANDARD_SIZE)

    logging.debug("Metadata file size: %s", str(metadata_size))

    # Open it for reading and return the data
    metadata_stream = metadata_file.read()
    metadata_bytes = metadata_stream.read_bytes(metadata_size)

    return metadata_bytes.get_data()
