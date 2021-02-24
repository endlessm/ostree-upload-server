import logging
import tempfile
import tarfile

from urllib.parse import urljoin
from urllib.request import pathname2url

from gi.repository import GLib

from os import makedirs, path, sep as path_separator
from shutil import rmtree

from .base import BaseImporter
from .util import find_repo, open_repository


class TarImporter(BaseImporter):
    MIME_TYPE = 'application/x-tar'

    TEMP_DIR_PREFIX = path.abspath(path.join(path_separator,
                                             'var',
                                             'tmp',
                                             'ostree-upload-server',
                                             'tar'))

    def _import_commit(self, commit, src_path_obj, target_repo):
        if not self._source_repo_path:
            raise RuntimeError("Cannot invoke _import_commit without calling "
                               "import_to_repo first")

        if not commit:
            raise RuntimeError("Cannot invoke _import_commit without valid "
                               "commit")

        logging.debug("Importing %s@%s into %s...", self._source_repo_path,
                      commit,
                      target_repo.get_path().get_uri())

        options = GLib.Variant('a{sv}', {
            'refs': GLib.Variant('as', [commit]),
            'inherit-transaction': GLib.Variant('b', True),
        })

        source_repo_uri = urljoin('file:',
                                  pathname2url(self._source_repo_path))
        target_repo.pull_with_options(source_repo_uri,
                                      options,
                                      None, None)

        logging.info("Importing complete.")

    def import_to_repo(self):
        logging.info('Trying to use %s extractor...', self.__class__.__name__)

        if not path.isdir(self.__class__.TEMP_DIR_PREFIX):
            makedirs(self.__class__.TEMP_DIR_PREFIX, 0o0755)

        dest_path = tempfile.mkdtemp('', prefix=self.__class__.__name__,
                                     dir=self.__class__.TEMP_DIR_PREFIX)
        try:
            logging.info('Extracting \'%s\' to a temp dir in %s...',
                         self._src_path, dest_path)
            with tarfile.open(self._src_path) as tar_archive:
                tar_archive.extractall(path=dest_path)

            self._source_repo_path = find_repo(dest_path)
            source_repo = open_repository(self._source_repo_path)

            refs = source_repo.list_refs().out_all_refs
            logging.debug("Refs: %r", refs)

            if not refs:
                logging.error("Could not find any refs in the source repo!")
                raise RuntimeError("Missing refs in Tar ostree repository!")

            # We only process the first ref in the repo provided - all
            # other variations are currently unsupported.
            if len(refs) > 1:
                error_msg = ("Multiple refs ({}) found in Tar archive!"
                             .format(refs))
                logging.error(error_msg)
                raise RuntimeError(error_msg)

            ref, commit = refs.popitem()
            logging.info("Ref: %s", ref)
            logging.info("Commit: %s", commit)

            self._apply_commit_to_repo(commit, ref)
        finally:
            if path.isdir(dest_path):
                logging.warning('Performing cleanup on %s...', dest_path)
                rmtree(dest_path)

        logging.info('Import complete of \'%s\' into %s!', self._src_path,
                     self._repo_path)


# This works with the same code so we just override the mimetype
class TgzImporter(TarImporter):
    MIME_TYPE = 'application/gzip'
