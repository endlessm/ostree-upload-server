import logging

from gevent import queue


class TaskQueue:
    def __init__(self):
        self._queue = queue.JoinableQueue()

        self._all_tasks = {}

    def add_task(self, task):
        task_id = task.get_id()

        logging.info('Adding task {}'.format(task_id))

        self._all_tasks[task_id] = task

        self._queue.put(task)

    def get_task(self, task_id):
        if not isinstance(task_id, int):
            raise Exception('Task IDs must be integers')

        return self._all_tasks.get(task_id)

    @property
    def queue(self):
        return self._queue

    def join(self, timeout=None):
        return self._queue.join(timeout)
