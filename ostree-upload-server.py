#!/usr/bin/env python2

import argparse
import atexit
import logging
import os
import tempfile
import threading

from functools import partial
from time import time

from gevent import Greenlet, queue
from gevent import sleep as gsleep
from gevent.event import Event
from gevent.pywsgi import WSGIServer
from gevent.subprocess import check_output, CalledProcessError, STDOUT

from flask import Flask, jsonify, request, render_template, send_from_directory, url_for

from pushadapters import adapters

MAINTENANCE_WAIT = 10


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


class TaskList:
    def __init__(self):
        self._queue = queue.JoinableQueue()
        self._all_tasks = {}

    def add_task(self, task):
        self._all_tasks[task.get_id()] = task
        self._queue.put(task)

    def get_queue(self):
        return self._queue

    def join(self, timeout=None):
        return self._queue.join(timeout)


class ThreadsafeCounter:
    def __init__(self):
        self._count = 0
        self._count_lock = threading.Lock()

    def __enter__(self):
        with self._count_lock:
            self._count += 1
            logging.debug("counter now " + str(self._count))
            return self._count

    def __exit__(self, type, value, traceback):
        with self._count_lock:
            self._count -= 1
            logging.debug("counter now " + str(self._count))

    def get_count(self):
        return self._count


class UploadWebApp(Flask):
    def __init__(self, import_name, upload_counter, webapp_callback):
        super(UploadWebApp, self).__init__(import_name)
        self._upload_counter = upload_counter
        self._webapp_callback = webapp_callback

        self.route("/")(self.index)
        self.route("/upload", methods=["GET", "POST"])(self.upload)
        self.route("/push")(self.push)

        self._tempdir = tempfile.mkdtemp(prefix="ostree-upload-server-")
        atexit.register(os.rmdir, self._tempdir)

    def index(self):
        return "<a href='{0}'>upload</a>".format(url_for("upload"))

    def upload(self):
        """
        Receive a flatpak bundle
        """
        if request.method == "POST":
            logging.debug("/upload: POST request start")

            with self._upload_counter:
                if 'file' not in request.files:
                    return "no file in POST\n", 400

                upload = request.files['file']
                if upload.filename == "":
                    return "no filename in upload\n", 400

                (f, real_name) = tempfile.mkstemp(dir=self._tempdir)
                os.close(f)
                upload.save(real_name)

                self._webapp_callback(upload.filename, real_name)
                logging.debug("/upload: POST request completed for " + upload.filename)

                return "task added\n"
        else:
            return "only POST method supported\n", 400

    def push(self):
        """
        Extract a flatpak bundle from local repository and push to a remote
        """
        try:
            ref = request.args['ref']
            remote = request.args['remote']
        except KeyError:
            return "ref and remote arguments required", 400
        logging.debug("/push: {0} to {1}".format(ref, remote))
        return("/push: {0} to {1}".format(ref, remote))


class Workers:
    def __init__(self, repo, completed_callback):
        self._workers = []
        self._repo = repo
        self._completed_callback = completed_callback

    def start(self, task_list, worker_count=4):
        self._exit_event = Event()
        for _ in range(worker_count):
            worker = Greenlet.spawn(self._work,
                                    task_list.get_queue(),
                                    self._exit_event)
            self._workers.append(worker)

    def stop(self):
        self._exit_event.set()

        for worker in self._workers:
            worker.join()

        self._exit_event.clear()

    def _work(self, task_queue, exit_event):
        global latest_task_complete

        count = 0
        logging.debug("worker started")

        while not self._exit_event.is_set():
            try:
                task = task_queue.get(timeout=1)
                task.set_state(TaskState.Processing)
                logging.info("processing task " + task.get_name())
                try:
                    output = check_output(["flatpak",
                                           "build-import-bundle",
                                           "--no-update-summary",
                                           self._repo,
                                           task.get_data()],
                                          stderr=STDOUT)
                    os.unlink(task.get_data())
                    task.set_state(TaskState.Completed)
                    logging.info("completed task " + task.get_name())
                except CalledProcessError as e:
                    # TODO: failed tasks should be handled - for now,
                    # don't delete the upload
                    task.set_state(TaskState.Failed)
                    logging.error("failed task " + task.get_name())
                    logging.error("task output: " + e.output)

                task_queue.task_done()
                self._completed_callback()

                count += 1
            except queue.Empty:
                pass

        logging.info("worker shutdown, " + str(count) + " items processed")


class OstreeUploadServer(object):
    def __init__(self, repo, port, workers):
        self._repo = repo
        self._port = port
        self._workers = workers

    def run(self):
        # Array since we need to pass by ref
        latest_task_complete = [time()]
        latest_maintenance_complete = time()
        active_upload_counter = ThreadsafeCounter()

        task_list = TaskList()

        logging.info("Starting server on %d..." % self._port)

        logging.debug("task completed callback %s", latest_task_complete);

        def completed_callback(latest_task_complete):
            logging.debug("task completed callback %s", latest_task_complete);
            latest_task_complete[:] = [time()]

        workers = Workers(self._repo, partial(completed_callback, latest_task_complete))
        workers.start(task_list, self._workers)

        def webapp_callback(task_name, filepath):
            task_list.add_task(Task(task_name, filepath))

        webapp = UploadWebApp(__name__, active_upload_counter, webapp_callback)

        http_server = WSGIServer(('', self._port), webapp)
        http_server.start()

        logging.info("Server started on %s" % self._port)

        # loop until interrupted
        while True:
            try:
                gsleep(5)
                task_list.join()
                logging.debug("task queue empty, " + str(active_upload_counter.get_count()) + " uploads ongoing")
                time_since_maintenance = time() - latest_maintenance_complete
                time_since_task = time() - latest_task_complete[0]
                logging.debug("{:.1f} complete".format(time_since_task))
                logging.debug("{:.1f} since last task, {:.1f} since last maintenance".format(
                            time_since_task,
                            time_since_maintenance))
                if time_since_maintenance > time_since_task:
                    # uploads have been processed since last maintenance
                    logging.debug("maintenance needed")
                    if time_since_task >= MAINTENANCE_WAIT:
                        logging.debug("idle, do maintenance")
                        workers.stop()

                        try:
                            output = check_output(["flatpak",
                                                   "build-update-repo",
                                                   "--generate-static-deltas",
                                                   "--prune",
                                                   self._repo],
                                                  stderr=STDOUT)
                            logging.info("completed maintenance: " + output)
                        except CalledProcessError as e:
                            logging.error("failed maintenance: " + e.output)

                        latest_maintenance_complete = time()
                        workers.start(task_list, self._workers)

            except (KeyboardInterrupt, SystemExit):
                break

        logging.info("Cleaning up resources...")

        http_server.stop()

        workers.stop()


if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="number of uploads to process in parallel")
    parser.add_argument("-p", "--port", type=int, default=5000,
                        help="HTTP server listen port")
    parser.add_argument("repo", help="OSTree repository")
    parser.add_argument("-v", "--verbose", help="output informational messages",
                    action="store_true")
    parser.add_argument("-d", "--debug", help="output debug messages",
                    action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.VERBOSE)
    else:
        logging.basicConfig(level=logging.INFO)

    OstreeUploadServer(args.repo,
                       args.port,
                       args.workers).run()
