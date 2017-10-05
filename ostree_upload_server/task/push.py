import logging
import os
import tempfile

from gevent.subprocess import check_output, CalledProcessError, STDOUT
from repolock import RepoLock

from ostree_upload_server.repolock import RepoLock
from ostree_upload_server.task.base import BaseTask
from ostree_upload_server.task.state import TaskState


class PushTask(BaseTask):
    def __init__(self, taskname, repo, ref, adapter, tempdir):
        super(PushTask, self).__init__(taskname)

        self._repo = repo
        self._ref = ref
        self._adapter = adapter
        self._tempdir = tempdir

    def run(self):
        logging.info("Processing task {}".format(self.get_name()))
        logging.debug("Push {0} to {1} ".format(self._ref, self._adapter))

        bundle = self._rebuild_bundle()
        if not bundle:
            logging.error("Failed to extract {0}".format(self._ref))
            self.set_state(TaskState.FAILED)
            return
        if not self._adapter.push(bundle):
            logging.error("Failed to push {0} to {1}".format(bundle, self._adapter))
            self.set_state(TaskState.FAILED)
            return

        os.unlink(bundle)

        self.set_state(TaskState.COMPLETED)

        logging.info("Completed task ".format(self.get_name()))

    def _rebuild_bundle(self):
        (f, filename) = tempfile.mkstemp(dir=self._tempdir)
        os.close(f)

        with RepoLock(self._repo):
            output = None
            try:
                output = check_output(["flatpak",
                                       "build-bundle",
                                       self._repo,
                                       filename,
                                       self._ref],
                                      stderr=STDOUT)
                logging.info("Extracted {0} as {1}".format(self._ref, filename))
                return filename
            except CalledProcessError as e:
                logging.info("Failed extraction {}".format(self.get_name()))

                logging.error("Failed task {}\n{}".format(e, e.output))
                os.unlink(filename)
                return None
            finally:
                if output:
                    logging.error("Task output: {}".format(output))
