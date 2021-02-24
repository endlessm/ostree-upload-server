import logging

from gevent import Greenlet, queue
from gevent.event import Event


class WorkerPoolExecutor:
    DEFAULT_WORKER_COUNT = 4

    def __init__(self, callback):
        self._callback = callback

        self._workers = []

    def start(self, task_queue, worker_count=DEFAULT_WORKER_COUNT):
        self._exit_event = Event()

        for _ in range(worker_count):
            worker = Greenlet.spawn(self._work,
                                    task_queue.queue,
                                    self._exit_event)
            self._workers.append(worker)

    def stop(self):
        self._exit_event.set()

        for worker in self._workers:
            worker.join()

        self._exit_event.clear()

    def _work(self, task_queue, exit_event):
        logging.debug("Worker started")

        processed_count = 0
        while not self._exit_event.is_set():
            try:
                task = task_queue.get(timeout=1)
                task.run()

                task_queue.task_done()

                self._callback()

                processed_count += 1
            except queue.Empty:
                pass

        logging.info("Worker shutdown, {} items processed"
                     .format(processed_count))
