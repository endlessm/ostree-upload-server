import logging
import os

from ostree_upload_server.bundle_importer import BundleImporter
from ostree_upload_server.repolock import RepoLock
from ostree_upload_server.task.base import BaseTask
from ostree_upload_server.task.state import TaskState


class ReceiveTask(BaseTask):
    def __init__(self, taskname, upload, repo):
        super(ReceiveTask, self).__init__(taskname)

        self._upload = upload
        self._repo = repo

    def run(self):
        logging.info("Processing task %s", self.get_name())

        self.set_state(TaskState.PROCESSING)

        with RepoLock(self._repo):
            try:
                logging.info("Trying to import %s into %s", self._upload,
                             self._repo)
                BundleImporter.import_bundle(self._upload, self._repo)
                self.set_state(TaskState.COMPLETED)

                logging.info("Completed task %s", self.get_name())
            except Exception as err:
                self.set_state(TaskState.FAILED)

                logging.error("Failed task %s", err)
            finally:
                # TODO: uploads are always deleted for now, but in the
                # future it might want to be kept for inspection for
                # failed tasks
                os.unlink(self._upload)
