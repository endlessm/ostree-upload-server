import logging

from abc import ABCMeta, abstractmethod

from gi.repository import GLib, Gio

from .util import (
    copy_commit, open_repository, update_repo_metadata, verify_commit_sig
)


class BaseImporter(object):
    __metaclass__ = ABCMeta

    def __init__(self, src_path, repository_path, gpg_homedir, keyring,
                 sign_key):
        self._src_path = src_path
        self._repo_path = repository_path
        self._gpg_homedir = gpg_homedir
        self._keyring = keyring
        self._sign_key = sign_key

    @property
    def MIME_TYPE(self):
        raise NotImplementedError()

    @abstractmethod
    def import_to_repo(self):
        pass

    @abstractmethod
    def _import_commit(self, commit, src_path_obj, target_repo):
        pass

    def _apply_commit_to_repo(self, commit, ref):
        target_repo = open_repository(self._repo_path)

        # Skip the rest of processing if the current ref is the same as
        # in delta
        _, current_rev = target_repo.resolve_rev(ref, allow_noent=True)
        logging.debug('Current %s commit: %s', ref, current_rev)
        if current_rev == commit:
            logging.info('Ref %s already at commit %s. Skipping changes.',
                         ref, commit)
            return

        # Prepare the transaction
        target_repo.prepare_transaction(None)

        # Apply the delta to our target repo
        try:
            src_path_obj = Gio.File.new_for_path(self._src_path)

            # Importer-specific way to get data from provided commit in
            # source repo into the target_repo
            self._import_commit(commit, src_path_obj, target_repo)

            # Verify that the commit signature is valid
            verify_commit_sig(target_repo, commit, self._gpg_homedir,
                              self._keyring)

            # Copy the commit to get correct collection and ref bindings
            new_commit = copy_commit(target_repo, commit, ref)

            # Sign this new commit
            if self._sign_key:
                logging.info("Signing with key %s from %s", self._sign_key,
                             self._gpg_homedir)
                try:
                    target_repo.sign_commit(commit_checksum=new_commit,
                                            key_id=self._sign_key,
                                            homedir=self._gpg_homedir,
                                            cancellable=None)
                except GLib.Error as err:
                    # Only ignore error if it's already signed with this key
                    if not err.matches(Gio.io_error_quark(),
                                       Gio.IOErrorEnum.EXISTS):
                        raise

                    logging.debug("Already signed with key %s", self._sign_key)

            # Set the ref to the new commit. Ideally this would use
            # transaction_set_collection_ref, but that's not available
            # on SOMA and the commit was set to use this repo's
            # collection ID, so it wouldn't make any difference.
            target_repo.transaction_set_ref(None, ref, new_commit)

            # Commit the transaction
            target_repo.commit_transaction(None)

        except:  # noqa: E722
            target_repo.abort_transaction(None)
            raise

        logging.info("updating summary...")
        update_repo_metadata(self._repo_path, self._gpg_homedir,
                             self._sign_key)
        logging.info("updating summary done...")
