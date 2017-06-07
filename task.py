from gevent import sleep as gsleep
from gevent.event import Event


class TaskState:
    Pending, Processing, Completed, Failed = range(4)


class Task:
    _next_task_id = 0

    def __init__(self, name, data):
        self._task_id = Task._next_task_id
        Task._next_task_id += 1
        self._name = name
        self._data = data
        self._state = TaskState.Pending
        self._state_change = Event()

    def set_state(self, newstate):
        self._state = newstate
        self._state_change.set()
        gsleep(0) # wake up anyone waiting
        self._state_change.clear()

    def get_name(self):
        return self._name

    def get_data(self):
        return self._data

    def get_state(self):
        return self._state

    def get_id(self):
        return self._task_id


