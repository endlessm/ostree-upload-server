import logging
import os

from gevent.subprocess import check_output, CalledProcessError, STDOUT

from ostree_upload_server.repolock import RepoLock
from ostree_upload_server.task.base import BaseTask
from ostree_upload_server.task.state import TaskState


class ReceiveTask(BaseTask):
    def __init__(self, taskname, upload, repo):
        super(ReceiveTask, self).__init__(taskname)

        self._upload = upload
        self._repo = repo

    def run(self):
        logging.info("Processing task {}".format(self.get_name()))

        self.set_state(TaskState.PROCESSING)

        with RepoLock(self._repo):
            output = None
            try:
                output = check_output(["./flatpak-import.py",
                                       "--debug",
                                       self._repo,
                                       self._upload],
                                      stderr=STDOUT)
                self.set_state(TaskState.COMPLETED)

                logging.info("Completed task {}".format(self.get_name()))
            except CalledProcessError as e:
                self.set_state(TaskState.FAILED)

                logging.error("Failed task {}\n{}".format(e, e.output))
            finally:
                if output:
                    logging.error("Task output: {}".format(output))
                # TODO: uploads are always deleted for now, but in the
                # future it might want to be kept for inspection for
                # failed tasks
                os.unlink(self._upload)
