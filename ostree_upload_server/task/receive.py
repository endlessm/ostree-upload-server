import logging
import os

from gevent.subprocess import check_output, CalledProcessError, STDOUT

from ostree_upload_server.flatpak_importer import FlatpakImporter
from ostree_upload_server.repolock import RepoLock
from ostree_upload_server.task.base import BaseTask
from ostree_upload_server.task.state import TaskState

import gi
gi.require_version('OSTree', '1.0')
from gi.repository import GLib


class ReceiveTask(BaseTask):
    def __init__(self, taskname, upload, repo):
        super(ReceiveTask, self).__init__(taskname)

        self._upload = upload
        self._repo = repo

    def run(self):
        logging.info("Processing task {}".format(self.get_name()))

        self.set_state(TaskState.PROCESSING)

        with RepoLock(self._repo):
            try:
                logging.info("Trying to import {} into {}".format(self._upload,
                                                                  self._repo))
                FlatpakImporter.import_flatpak(self._upload, self._repo)
                self.set_state(TaskState.COMPLETED)

                logging.info("Completed task {}".format(self.get_name()))
            except GLib.Error as e:
                self.set_state(TaskState.FAILED)

                logging.error("Failed task {}".format(e))
            finally:
                # TODO: uploads are always deleted for now, but in the
                # future it might want to be kept for inspection for
                # failed tasks
                os.unlink(self._upload)
