#!/usr/bin/env python2

import atexit
import os
import tempfile
from time import time

from gevent import Greenlet
from gevent import sleep as gsleep
from gevent.queue import JoinableQueue, Empty
from gevent.event import Event
from gevent.pywsgi import WSGIServer
from gevent.subprocess import Popen, PIPE

from flask import Flask, jsonify, request, render_template, send_from_directory

PORT = 5000


# TODO: Turn this into a class
def worker(queue, quit):
    count = 0
    while not quit.is_set():
        try:
            task = queue.get(timeout=1)
            task.set_state(TaskState.Processing)
            print("processing task " + task.name)
            sub = Popen(["flatpak build-import-bundle repo " + task.data], stdout=PIPE, shell=True)
            out, err = sub.communicate()
            os.unlink(task.data)
            task.set_state(TaskState.Completed)
            queue.task_done()
            print("completed task " + task.name + " with return code " + str(sub.returncode))
            count += 1
        except Empty:
            pass
    print("worker shutdown, " + str(count) + " items processed")

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


app = Flask(__name__)
tempdir = tempfile.mkdtemp("upload")
atexit.register(os.rmdir, tempdir)
app.config["UPLOAD_FOLDER"] = tempdir

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
        if 'file' not in request.files:
            return "no file in POST\n"
        upload = request.files['file']
        if upload.filename == "":
            return "no filename in upload\n"
        (f, real_name) = tempfile.mkstemp(dir=app.config['UPLOAD_FOLDER'])
        os.close(f)
        upload.save(real_name)
        task_list.add_task(Task(upload.filename, real_name))
        return "task added\n"

class Workers:
    def __init__(self):
        self.workers = []
        self.quit_workers = Event()
    def start(self, task_list, worker_func, worker_count=4):
        for i in range(worker_count):
            w = Greenlet.spawn(worker_func, task_list.get_queue(), self.quit_workers)
            self.workers.append(w)
    def stop(self):
        self.quit_workers.set()
        for w in self.workers:
            w.join()

if __name__=='__main__':
    print("Starting server on %d..." % PORT)

    # TODO: Add argparse for these settings
    worker_count = 4

    workers = Workers()
    workers.start(task_list, worker)

    http_server = WSGIServer(('', PORT), app)
    http_server.start()

    print("Server started on %s" % PORT)

    # loop until interrupted
    while True:
        try:
            gsleep(5)
            task_list.join()
        except (KeyboardInterrupt, SystemExit):
            break

    print("Cleaning up resources...")

    http_server.stop()

    workers.stop()
