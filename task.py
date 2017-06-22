import logging
import os
import requests
import tempfile

from gevent import sleep as gsleep
from gevent.event import Event
from gevent.subprocess import check_output, CalledProcessError, STDOUT

from repolock import RepoLock

class TaskState:
    PENDING, PROCESSING, COMPLETED, FAILED = range(4)


class BaseTask(object):
    _next_task_id = 0

    def __init__(self, name):
        self._task_id = BaseTask._next_task_id
        BaseTask._next_task_id += 1
        self._name = name
        self._state = TaskState.PENDING
        self._state_change = Event()

    def set_state(self, newstate):
        if newstate == self._state:
            return
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


def _import_flatpak(repo, filename):
    with RepoLock(repo):
        try:
            output = check_output(["./flatpak-import.py",
                                   "--debug",
                                   repo,
                                   filename],
                                  stderr=STDOUT)
            logging.debug("flatpak import output: " + output)
            return True
        except CalledProcessError as e:
            logging.error("flatpak import output: " + e.output)
            return False


class ReceiveTask(BaseTask):
    def __init__(self, taskname, upload, repo):
        super(ReceiveTask, self).__init__(taskname)
        self._upload = upload
        self._repo = repo

    def run(self):
        self.set_state(TaskState.PROCESSING)
        logging.info("processing task " + self.get_name())
        if _import_flatpak(self._repo, self._upload):
            os.unlink(self._upload)
            logging.info("completed task " + self.get_name())
            self.set_state(TaskState.COMPLETED)
        else:
            # TODO: failed tasks should be handled - for now,
            # don't delete the upload
            logging.error("failed task " + self.get_name())
            self.set_state(TaskState.FAILED)


class FetchTask(BaseTask):
    def __init__(self, taskname, url, repo, tempdir):
        super(FetchTask, self).__init__(taskname)
        self._url = url
        self._repo = repo
        self._tempdir = tempdir

    def run(self):
        self.set_state(TaskState.PROCESSING)
        logging.info("processing task " + self.get_name())
        (fd, filename) = tempfile.mkstemp(dir=self._tempdir)
        os.close(fd)
        r = requests.get(self._url, stream=True)
        with open(filename, "wb") as f:
            for chunk in r.iter_content(chunk_size=128):
                f.write(chunk)
        if _import_flatpak(self._repo, filename):
            os.unlink(filename)
            logging.info("completed task " + self.get_name())
            self.set_state(TaskState.COMPLETED)
        else:
            # TODO: failed tasks should be handled - for now,
            # don't delete the upload
            logging.error("failed task " + self.get_name())
            self.set_state(TaskState.FAILED)


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
            self.set_state(TaskState.FAILED)
            return
        if not self._adapter.push(bundle):
            logging.error("failed to push {0} to {1}".format(bundle, self._adapter))
            self.set_state(TaskState.FAILED)
            return
        os.unlink(bundle)
        self.set_state(TaskState.COMPLETED)
        logging.info("completed task " + self.get_name())

    def _extract(self):
        (f, filename) = tempfile.mkstemp(dir=self._tempdir)
        os.close(f)
        with RepoLock(self._repo):
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
