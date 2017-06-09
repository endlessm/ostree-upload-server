import logging
import os
import tempfile

from gevent import sleep as gsleep
from gevent.event import Event
from gevent.subprocess import check_output, CalledProcessError, STDOUT

class TaskState:
    Pending, Processing, Completed, Failed = range(4)


class BaseTask(object):
    _next_task_id = 0

    def __init__(self, name):
        self._task_id = BaseTask._next_task_id
        BaseTask._next_task_id += 1
        self._name = name
        self._state = TaskState.Pending
        self._state_change = Event()

    def set_state(self, newstate):
        self._state = newstate
        self._state_change.set()
        gsleep(0) # wake up anyone waiting
        self._state_change.clear()

    def get_name(self):
        return self._name

    def get_state(self):
        return self._state

    def get_id(self):
        return self._task_id


class ReceiveTask(BaseTask):
    def __init__(self, taskname, upload, repo):
        super(ReceiveTask, self).__init__(taskname)
        self._upload = upload
        self._repo = repo

    def run(self):
        self.set_state(TaskState.Processing)
        logging.info("processing task " + self.get_name())
        try:
            output = check_output(["flatpak",
                                   "build-import-bundle",
                                   "--no-update-summary",
                                   self._repo,
                                   self._upload],
                                  stderr=STDOUT)
            os.unlink(self._upload)
            self.set_state(TaskState.Completed)
            logging.info("completed task " + self.get_name())
        except CalledProcessError as e:
            # TODO: failed tasks should be handled - for now,
            # don't delete the upload
            self.set_state(TaskState.Failed)
            logging.error("failed task " + self.get_name())
            logging.error("task output: " + e.output)


class PushTask(BaseTask):
    def __init__(self, taskname, repo, ref, adapter, tempdir):
        super(PushTask, self).__init__(taskname)
        self._repo = repo
        self._ref = ref
        self._adapter = adapter
        self._tempdir = tempdir

    def run(self):
        logging.info("processing task " + self.get_name())
        logging.debug("push {0} to {1} ".format(self._ref, self._adapter))
        bundle = self._extract()
        if not bundle:
            logging.error("failed to extract {0}".format(self._ref))
            self.set_state(TaskState.Failed)
            return
        if not self._adapter.push(bundle):
            logging.error("failed to push {0} to {1}".format(bundle, self._adapter))
            self.set_state(TaskState.Failed)
            return
        os.unlink(bundle)
        self.set_state(TaskState.Completed)
        logging.info("completed task " + self.get_name())

    def _extract(self):
        (f, filename) = tempfile.mkstemp(dir=self._tempdir)
        os.close(f)
        try:
            output = check_output(["flatpak",
                                   "build-bundle",
                                   self._repo,
                                   filename,
                                   self._ref],
                                  stderr=STDOUT)
            logging.info("extracted {0} as {1}".format(self._ref, filename))
            return filename
        except CalledProcessError as e:
            os.unlink(filename)
            return None
