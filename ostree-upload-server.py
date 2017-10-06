#!/usr/bin/env python2

import argparse
import atexit
import logging
import os
import shutil
import tempfile

from ConfigParser import SafeConfigParser
from functools import partial
from time import time

from gevent import Greenlet
from gevent import sleep as gsleep
from gevent.event import Event
from gevent.pywsgi import WSGIServer
from gevent.subprocess import check_output, CalledProcessError, STDOUT

from flask import Flask, json, jsonify, request, Response, url_for
from flask_api import status

from ostree_upload_server.authenticator import Authenticator
from ostree_upload_server.push_adapter.dummy import DummyPushAdapter
from ostree_upload_server.push_adapter.http import HttpPushAdapter
from ostree_upload_server.push_adapter.scp import ScpPushAdapter
from ostree_upload_server.repolock import RepoLock
from ostree_upload_server.task.push import PushTask
from ostree_upload_server.task.receive import ReceiveTask
from ostree_upload_server.task.state import TaskState
from ostree_upload_server.task_queue import TaskQueue
from ostree_upload_server.threadsafe_counter import ThreadsafeCounter
from ostree_upload_server.worker_pool_executor import WorkerPoolExecutor


DEFAULT_LISTEN_PORT = 5000
MAINTENANCE_WAIT = 10

global latest_task_complete


class UploadWebApp(Flask):
    def __init__(self, import_name, users, repo, upload_counter,
                 remote_push_adapter_map, task_queue):
        super(UploadWebApp, self).__init__(import_name)
        self._authenticator = Authenticator(users)
        self._repo = repo
        self._upload_counter = upload_counter
        self._remote_push_adapter_map = remote_push_adapter_map
        self._task_queue = task_queue

        # Sanity check
        if not os.path.isdir(self._repo):
            raise RuntimeError("ERROR! Repo path '{}' is not valid!".format(self._repo))

        self.route("/")(self.index)
        self.route("/upload", methods=["GET", "POST"])(self.upload)
        self.route("/push", methods=["GET", "PUT"])(self.push)

        # These files might be huge and /tmp might be mounted on tmpfs
        # so to avoid RAM exhaustion, we use /var/tmp
        self._tempdir = tempfile.mkdtemp(dir="/var/tmp", prefix="ostree-upload-server-")
        atexit.register(shutil.rmtree, self._tempdir)

    def _request_authentication(self):
        """Sends a 401 response that enables basic auth"""
        auth_message = { 'success': False,
                         'message': "Authentication required" }
        return Response(json.dumps(auth_message),
                        401,
                        {'WWW-Authenticate': 'Basic realm="Login Required"'})

    def _get_request_task(self, allowed_task):
        """Return the state of a requested task

        allowed_task is the allowed BaseTask subclass used for the
        associated route.
        """
        # Parse the task parameter
        try:
            task_id = int(request.args['task'])
        except KeyError:
            return self._response(400, "Task argument required")
        except ValueError:
            return self._response(400, "Task argument must be integer")

        # Lookup the task ID
        task = self._task_queue.get_task(task_id)

        if task is None:
            return self._response(404,
                                  "Task {} does not exist".format(task_id))

        if not isinstance(task, allowed_task):
            return self._response(400,
                                  "Task {} is not a {} task".format(task_id,
                                                                    request.path))

        # Format the task state
        state = task.get_state_name()
        msg = 'Task {} state is {}'.format(task_id, state)
        return self._response(200, msg, state=state)

    def index(self):
        links = "<a href='{0}'>upload</a>".format(url_for("upload"))
        links += "\n<a href='{0}'>upload</a>".format(url_for("upload"))
        return links

    def upload(self):
        """
        Receive a flatpak bundle
        """
        if not self._authenticator.authenticate(request):
            return self._request_authentication()

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

                task = ReceiveTask(upload.filename, real_name,
                                   self._repo)
                self._task_queue.add_task(task)
                logging.debug("/upload: POST request completed for " + upload.filename)

                return self._response(200, "Importing bundle",
                                      task=task.get_id())
        elif request.method == "GET":
            logging.debug("/upload: GET request {}"
                          .format(request.full_path))
            return self._get_request_task(ReceiveTask)
        else:
            return self._response(400,
                                  "Only GET and POST methods supported")

    def push(self):
        """
        Extract a flatpak bundle from local repository and push to a remote
        """
        if not self._authenticator.authenticate(request):
            return self._request_authentication()

        logging.debug(request.args)
        if request.method == 'PUT':
            try:
                ref = request.args['ref']
                remote = request.args['remote']
            except KeyError:
                return self._response(400,
                                      "ref and remote arguments required")
            logging.debug("/push: {0} to {1}".format(ref, remote))
            if not remote in self._remote_push_adapter_map:
                return self._response(400,
                                      "Remote is not in the whitelist")
            adapter = self._remote_push_adapter_map[remote]
            task = PushTask(ref, self._repo, ref, adapter, self._tempdir)
            self._task_queue.add_task(task)
            return self._response(200,
                                  "Pushing {0} to {1}".format(ref, remote),
                                  task=task.get_id())
        elif request.method == "GET":
            logging.debug("/push: GET request {}"
                          .format(request.full_path))
            return self._get_request_task(PushTask)
        else:
            return self._response(400,
                                  "Only GET and PUT methods supported")

    def _response(self, status_code, message, **kwargs):
        body = {
            'success': status.is_success(status_code),
            'message': message,
        }
        body.update(kwargs)
        return jsonify(body), status_code


class OstreeUploadServer(object):
    CONFIG_LOCATIONS = [ '/etc/ostree/ostree-upload-server.conf',
                         os.path.expanduser('~/.config/ostree/ostree-upload-server.conf'),
                         'ostree-upload-server.conf' ]

    ADAPTER_IMPL_CLASSES = [ DummyPushAdapter,
                             HttpPushAdapter,
                             ScpPushAdapter ]

    def __init__(self, repo_path, port, workers):
        self._repo = repo_path
        self._port = port
        self._workers = workers

        self._adapters = {}
        for adapter_impl_class in OstreeUploadServer.ADAPTER_IMPL_CLASSES:
            self._adapters[adapter_impl_class.name] = adapter_impl_class

    def run(self):
        # Array since we need to pass by ref
        latest_task_complete = [time()]
        latest_maintenance_complete = time()
        active_upload_counter = ThreadsafeCounter()

        task_queue = TaskQueue()

        logging.info("Starting server on %d..." % self._port)

        logging.debug("task completed callback %s", latest_task_complete)

        def completed_callback(latest_task_complete):
            logging.debug("task completed callback %s", latest_task_complete)
            latest_task_complete[:] = [time()]

        workers = WorkerPoolExecutor(partial(completed_callback, latest_task_complete))
        workers.start(task_queue, self._workers)

        remote_push_adapter_map = {}

        config = SafeConfigParser(allow_no_value = True)
        config.read(OstreeUploadServer.CONFIG_LOCATIONS)

        for section in config.sections():
            if not section.startswith('remote-'):
                continue
            remote_dict = dict(config.items(section))
            remote_name = section.split('-')[1]
            adapter_type = remote_dict['type']
            if adapter_type in self._adapters.keys():
                logging.debug("Setting up adapter {0}, type {1}".format(remote_name,
                                                                        adapter_type))
                adapter_impl_class = self._adapters[adapter_type]
                remote_push_adapter_map[remote_name] = adapter_impl_class(remote_name,
                                                                          remote_dict)
            else:
                logging.error("adapter {0}: unknown type {1}".format(remote_name, adapter_type))

        users = None

        if config.has_section('users'):
            users = dict(config.items('users'))

        if users:
            logging.debug("Users configured:")
            for user in users.keys():
                logging.debug(" - {}".format(user))
        else:
            logging.warning("Warning! No authentication configured!")

        # Perform idle maintenance?
        do_maintenance = True
        if config.has_section('server'):
            do_maintenance = config.getboolean('server', 'maintenance')

        webapp = UploadWebApp(__name__,
                              users,
                              self._repo,
                              active_upload_counter,
                              remote_push_adapter_map,
                              task_queue)

        http_server = WSGIServer(('', self._port), webapp)
        http_server.start()

        logging.info("Server started on %s" % self._port)

        # loop until interrupted
        while True:
            try:
                gsleep(5)
                task_queue.join()
                logging.debug("task queue empty, " + str(active_upload_counter.count) + " uploads ongoing")

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
                        workers.start(task_queue, self._workers)

            except (KeyboardInterrupt, SystemExit):
                break

        logging.info("Cleaning up resources...")

        http_server.stop()

        workers.stop()


if __name__=='__main__':
    parser = argparse.ArgumentParser()

    parser.add_argument("repo",
                        help="OSTree repository")
    parser.add_argument("-w", "--workers", type=int,
                        default=WorkerPoolExecutor.DEFAULT_WORKER_COUNT,
                        help="Number of uploads to process in parallel")
    parser.add_argument("-p", "--port", type=int,
                        default=DEFAULT_LISTEN_PORT,
                        help="HTTP server listen port")

    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Output informational messages")
    parser.add_argument("-d", "--debug", action="store_true",
                        help="Output debug messages")

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    OstreeUploadServer(args.repo, args.port, args.workers).run()
