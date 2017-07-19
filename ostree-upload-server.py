#!/usr/bin/env python2

import argparse
import atexit
import logging
import os
import tempfile
import threading

from ConfigParser import SafeConfigParser
from functools import partial
from passlib.hash import pbkdf2_sha256
from time import time

from gevent import Greenlet, queue
from gevent import sleep as gsleep
from gevent.event import Event
from gevent.pywsgi import WSGIServer
from gevent.subprocess import check_output, CalledProcessError, STDOUT

from flask import Flask, json, jsonify, request, Response, url_for
from flask_api import status

from push_adapters import adapter_types
from repolock import RepoLock
from task import TaskState, ReceiveTask, PushTask

MAINTENANCE_WAIT = 10


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
    def __init__(self, import_name, users, repo, upload_counter, push_adapters, webapp_callback):
        super(UploadWebApp, self).__init__(import_name)
        self._users = users
        self._repo = repo
        self._upload_counter = upload_counter
        self._push_adapters = push_adapters
        self._webapp_callback = webapp_callback

        self.route("/")(self.index)
        self.route("/upload", methods=["GET", "POST"])(self.upload)
        self.route("/push")(self.push)

        self._tempdir = tempfile.mkdtemp(prefix="ostree-upload-server-")
        atexit.register(os.rmdir, self._tempdir)

    def _check_auth(self):
        if not self._users:
            return True
        auth = request.authorization
        if not auth:
            return False
        if auth.username not in self._users:
            return False

        # Check the PBKDF2-SHA256 encrypted password
        hashed_password = self._users[auth.username]
        if not pbkdf2_sha256.identify(hashed_password):
            logging.warning('Hashed password for user {} is not '
                            'valid for PBKDF2-SHA256 algorithm'
                            .format(auth.username))
            return False
        if not pbkdf2_sha256.verify(auth.password, hashed_password):
            return False
        return True

    def _authenticate(self):
        """Sends a 401 response that enables basic auth"""
        message = { 'success': False, 'message': "Authentication required" }
        return Response(
            json.dumps(message), 401,
            {'WWW-Authenticate': 'Basic realm="Login Required"'})

    def index(self):
        return "<a href='{0}'>upload</a>".format(url_for("upload"))
        return "<a href='{0}'>push</a>".format(url_for("push"))

    def upload(self):
        """
        Receive a flatpak bundle
        """
        if not self._check_auth():
            return self._authenticate()

        if request.method == "POST":
            logging.debug("/upload: POST request start")

            with self._upload_counter:
                if 'file' not in request.files:
                    return self._response(400, "No file in request")

                upload = request.files['file']
                if upload.filename == "":
                    return self._response(400, "No filename in request")

                (f, real_name) = tempfile.mkstemp(dir=self._tempdir)
                os.close(f)
                upload.save(real_name)

                self._webapp_callback(ReceiveTask(upload.filename, real_name, self._repo))
                logging.debug("/upload: POST request completed for " + upload.filename)

                # TODO: should return a task ID that can be used to check task status
                return self._response(200, "Importing bundle")
        else:
            return self._response(400, "Only POST method is suppported")

    def push(self):
        """
        Extract a flatpak bundle from local repository and push to a remote
        """
        if not self._check_auth():
            return self._authenticate()

        logging.debug(request.args)
        try:
            ref = request.args['ref']
            remote = request.args['remote']
        except KeyError:
            return self._response(400, "ref and remote arguments required")
        logging.debug("/push: {0} to {1}".format(ref, remote))
        if not remote in self._push_adapters:
            return self._response(400, "Remote is not in the whitelist")
        adapter = self._push_adapters[remote]
        self._webapp_callback(PushTask(ref, self._repo, ref, adapter, self._tempdir))
        # TODO: should return a task ID that can be used to check task status
        return self._response(200, "Pushing {0} to {1}".format(ref, remote))

    def _response(self, status_code, message):
        return jsonify( { 'success': status.is_success(status_code),
                          'message': message } ), status_code


class Workers:
    def __init__(self, completed_callback):
        self._workers = []
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
                task.run()
                task_queue.task_done()
                self._completed_callback()

                count += 1
            except queue.Empty:
                pass

        logging.info("worker shutdown, " + str(count) + " items processed")


class OstreeUploadServer(object):
    def __init__(self, repo_path, port, workers):
        self._repo = repo_path
        self._port = port
        self._workers = workers

    def run(self):
        # Array since we need to pass by ref
        latest_task_complete = [time()]
        latest_maintenance_complete = time()
        active_upload_counter = ThreadsafeCounter()

        task_list = TaskList()

        logging.info("Starting server on %d..." % self._port)

        logging.debug("task completed callback %s", latest_task_complete)

        def completed_callback(latest_task_complete):
            logging.debug("task completed callback %s", latest_task_complete)
            latest_task_complete[:] = [time()]

        workers = Workers(partial(completed_callback, latest_task_complete))
        workers.start(task_list, self._workers)

        push_adapters = {}
        config = SafeConfigParser(allow_no_value = True)
        config.read([
            '/etc/ostree/ostree-upload-server.conf',
            os.path.expanduser('~/.config/ostree/ostree-upload-server.conf'),
            'ostree-upload-server.conf'
        ])
        for section in config.sections():
            if not section.startswith('remote-'):
                continue
            remote_dict = dict(config.items(section))
            remote_name = section.split('-')[1]
            adapter_type = remote_dict['type']
            if adapter_type in adapter_types:
                logging.debug("setting up adapter {0}, type {1}".format(remote_name, adapter_type))
                push_adapters[remote_name] = (adapter_types[adapter_type])(remote_name, remote_dict)
            else:
                logging.error("adapter {0}: unknown type {1}".format(remote_name, adapter_type))

        def webapp_callback(task):
            task_list.add_task(task)

        users = None

        if config.has_section('users'):
            users = dict(config.items('users'))

        # Perform idle maintenance?
        do_maintenance = True
        if config.has_section('server'):
            do_maintenance = config.getboolean('server', 'maintenance')

        webapp = UploadWebApp(__name__,
                              users,
                              self._repo,
                              active_upload_counter,
                              push_adapters,
                              webapp_callback)

        http_server = WSGIServer(('', self._port), webapp)
        http_server.start()

        logging.info("Server started on %s" % self._port)

        # loop until interrupted
        while True:
            try:
                gsleep(5)
                task_list.join()
                logging.debug("task queue empty, " + str(active_upload_counter.get_count()) + " uploads ongoing")

                # Continue looping if maintenance not desired
                if not do_maintenance:
                    continue

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

                        with RepoLock(self._repo, exclusive=True):
                            try:
                                output = check_output(["flatpak",
                                                       "build-update-repo",
                                                       "--generate-static-deltas",
                                                       "--prune",
                                                       self._repo],
                                                      stderr=STDOUT)
                                logging.info("completed maintenance: " + output)
                            except CalledProcessError as e:
                                logging.error("ERROR! Maintenance task failed!")
                                logging.error(e)

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
