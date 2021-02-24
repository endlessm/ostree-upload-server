from gevent import sleep as gsleep
from abc import ABCMeta, abstractmethod

from gevent.event import Event

from ostree_upload_server.task.state import TaskState


class BaseTask:
    __metaclass__ = ABCMeta

    _next_task_id = 0

    def __init__(self, name):
        self._name = name
        self._state = TaskState.PENDING
        self._state_change = Event()

        self._task_id = BaseTask._next_task_id
        BaseTask._next_task_id += 1

    def set_state(self, state):
        self._state = state
        self._state_change.set()

        # Wake up anyone waiting
        gsleep(0)

        self._state_change.clear()

    def get_name(self):
        return self._name

    def get_state(self):
        return self._state

    def get_state_name(self):
        return TaskState.name(self._state)

    def get_id(self):
        return self._task_id

    @abstractmethod
    def run(self):
        raise NotImplementedError('Cannot invoke BaseTask.run() method!')
