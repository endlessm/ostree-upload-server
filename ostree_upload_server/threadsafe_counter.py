import logging
import threading


class ThreadsafeCounter(object):
    def __init__(self):
        self._count = 0
        self._count_lock = threading.Lock()

    def __enter__(self):
        with self._count_lock:
            self._count += 1
            logging.debug("Counter now " + str(self._count))
            return self._count

    def __exit__(self, type, value, traceback):
        with self._count_lock:
            self._count -= 1
            logging.debug("Counter now " + str(self._count))

    @property
    def count(self):
        return self._count
