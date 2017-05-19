#!/usr/bin/env python2

import argparse
import atexit
import logging
import os
import tempfile
from time import time

from gevent import Greenlet
from gevent import sleep as gsleep
from gevent.lock import BoundedSemaphore
from gevent.queue import JoinableQueue, Empty
from gevent.event import Event
from gevent.pywsgi import WSGIServer
from gevent.subprocess import check_output, CalledProcessError, STDOUT

from flask import Flask, jsonify, request, render_template, send_from_directory

MAINTENANCE_WAIT = 10


# TODO: Turn this into a class
def worker(queue, quit):
    global latest_task_complete
    count = 0
    logging.debug("worker started")
    while not quit.is_set():
        try:
            task = queue.get(timeout=1)
            task.set_state(TaskState.Processing)
            logging.info("processing task " + task.name)
            try:
                output = check_output(["flatpak",
                                       "build-import-bundle",
                                       "--no-update-summary",
                                       "repo",
                                       task.data],
                                      stderr=STDOUT)
                os.unlink(task.data)
                task.set_state(TaskState.Completed)
                logging.info("completed task " + task.name)
            except CalledProcessError as e:
                # TODO: failed tasks should be handled - for now,
                # don't delete the upload
                task.set_state(TaskState.Failed)
                logging.error("failed task " + task.name)
                logging.error("task output: " + e.output)
            queue.task_done()
            latest_task_complete = time()
            count += 1
        except Empty:
            pass
    logging.info("worker shutdown, " + str(count) + " items processed")

class TaskState:
    Pending, Processing, Completed, Failed = range(4)

class Task:
    next_task_id = 0
    def __init__(self, name, data):
        self.task_id = Task.next_task_id
        Task.next_task_id += 1
        self.name = name
        self.data = data
        self.state = TaskState.Pending
        self.state_change = Event()

    def set_state(self, newstate):
        self.state = newstate
        self.state_change.set()
        gsleep(0) # wake up anyone waiting
        self.state_change.clear()

    def get_state(self):
        return self.state

    def get_id(self):
        return self.task_id

    def wait_for_state_change(self, timeout=None):
        return self.state_change.wait(timeout)

class TaskList:
    def __init__(self):
        self.queue = JoinableQueue()
        self.all_tasks = {}

    def add_task(self, task):
        self.all_tasks[task.get_id()] = task
        self.queue.put(task)

    def get_queue(self):
        return self.queue

    def join(self, timeout=None):
        return self.queue.join(timeout)

class Counter:
    def __init__(self):
        self.count = 0
        self.count_lock = BoundedSemaphore(1)

    def __enter__(self):
        with self.count_lock:
            self.count += 1
            logging.debug("counter now " + str(self.count))
            return self.count

    def __exit__(self, type, value, traceback):
        with self.count_lock:
            self.count -= 1
            logging.debug("counter now " + str(self.count))

app = Flask(__name__)
tempdir = tempfile.mkdtemp(prefix="ostree-upload-server-")
atexit.register(os.rmdir, tempdir)
app.config["UPLOAD_FOLDER"] = tempdir

latest_task_complete = time()
latest_maintenance_complete = time()
active_upload_counter = Counter()
task_list = TaskList()

# TODO: Turn server into an isolated class
@app.route("/")
def main():
    """
    Main web site entry point.
    """
    return "hello world"

@app.route("/upload", methods=["GET", "POST"])
def upload_bundle():
    """
    Upload a flatpak bundle
    """
    if request.method == "POST":
        logging.debug("/upload: POST request start")
        with active_upload_counter:
            if 'file' not in request.files:
                return "no file in POST\n", 400
            upload = request.files['file']
            if upload.filename == "":
                return "no filename in upload\n", 400
            (f, real_name) = tempfile.mkstemp(dir=app.config['UPLOAD_FOLDER'])
            os.close(f)
            upload.save(real_name)
            task_list.add_task(Task(upload.filename, real_name))
            logging.debug("/upload: POST request completed for " + upload.filename)
            return "task added\n"

class Workers:
    def __init__(self):
        self.workers = []
        self.quit_workers = Event()
    def start(self, task_list, worker_func, worker_count=4):
        for i in range(worker_count):
            worker = Greenlet.spawn(worker_func,
                                    task_list.get_queue(),
                                    self.quit_workers)
            self.workers.append(worker)
    def stop(self):
        self.quit_workers.set()
        for w in self.workers:
            w.join()
        self.quit_workers.clear()

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="number of uploads to process in parallel")
    parser.add_argument("-p", "--port", type=int, default=5000,
                        help="HTTP server listen port")
    parser.add_argument("-v", "--verbose", help="output informational messages",
                    action="store_true")
    parser.add_argument("-d", "--debug", help="output debug messages",
                    action="store_true")
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.INFO)

    logging.info("Starting server on %d..." % args.port)

    workers = Workers()
    workers.start(task_list, worker, args.workers)

    http_server = WSGIServer(('', args.port), app)
    http_server.start()

    logging.info("Server started on %s" % args.port)

    # loop until interrupted
    while True:
        try:
            gsleep(5)
            task_list.join()
            logging.debug("task queue empty, " + str(active_upload_counter.count) + " uploads ongoing")
            time_since_maintenance = time() - latest_maintenance_complete
            time_since_task = time() - latest_task_complete
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
                                               "repo"],
                                              stderr=STDOUT)
                        logging.info("completed maintenance: " + output)
                    except CalledProcessError as e:
                        logging.error("failed maintenance: " + e.output)

                    latest_maintenance_complete = time()
                    workers.start(task_list, worker, args.workers)

        except (KeyboardInterrupt, SystemExit):
            break

    logging.info("Cleaning up resources...")

    http_server.stop()

    workers.stop()