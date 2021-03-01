#!/usr/bin/env python3

import argparse
import atexit
import errno
import logging
import os
import shutil
import tempfile

from configparser import ConfigParser
from functools import partial
from time import time

from gevent import sleep as gsleep
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
from ostree_upload_server.task_queue import TaskQueue
from ostree_upload_server.threadsafe_counter import ThreadsafeCounter
from ostree_upload_server.worker_pool_executor import WorkerPoolExecutor


DEFAULT_LISTEN_PORT = 5000
MAINTENANCE_WAIT = 10

# Flask (really werzkeug) saves uploads with TemporaryFile, so they go
# in tempfile.tempdir. Our uploads can be very large, so make that
# /var/tmp in case /tmp is a tmpfs.
tempfile.tempdir = '/var/tmp'

global latest_task_complete


class UploadWebApp(Flask):
    def __init__(self, import_name, users, repos, upload_counter,
                 remote_push_adapter_map, import_config, task_queue):
        super(UploadWebApp, self).__init__(import_name)
        self._authenticator = Authenticator(users)
        self._repos = repos
        self._upload_counter = upload_counter
        self._remote_push_adapter_map = remote_push_adapter_map
        self._import_config = import_config
        self._task_queue = task_queue

        self.route("/")(self.__class__.index)
        self.route("/upload", methods=["GET", "POST"])(self.upload)
        self.route("/push", methods=["GET", "PUT"])(self.push)

        # These files might be huge and /tmp might be mounted on tmpfs
        # so to avoid RAM exhaustion, we use /var/tmp
        self._tempdir = tempfile.mkdtemp(dir="/var/tmp",
                                         prefix="ostree-upload-server-")
        atexit.register(shutil.rmtree, self._tempdir)

    @staticmethod
    def request_authentication():
        """Sends a 401 response that enables basic auth"""
        auth_message = {'success': False,
                        'message': "Authentication required"}
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
            return self.build_response(400, "Task argument required")
        except ValueError:
            return self.build_response(400, "Task argument must be integer")

        # Lookup the task ID
        task = self._task_queue.get_task(task_id)

        if task is None:
            return self.build_response(
                404, "Task {} does not exist".format(task_id))

        if not isinstance(task, allowed_task):
            err_message = "Task {} is not a {} task".format(task_id,
                                                            request.path)
            return self.build_response(400, err_message)

        # Format the task state
        state = task.get_state_name()
        msg = 'Task {} state is {}'.format(task_id, state)
        return self.build_response(200, msg, state=state)

    @staticmethod
    def index():
        links = "<a href='{0}'>upload</a>".format(url_for("upload"))
        links += "<br /><a href='{0}'>push</a>".format(url_for("push"))
        return links

    def upload(self):
        """
        Handler for receiving a bundle
        """

        # Makes invocations of static methods shorter
        cls = self.__class__

        if not self._authenticator.authenticate(request):
            return cls.request_authentication()

        if request.method == "POST":
            logging.debug("/upload: POST request start")

            with self._upload_counter:
                if 'file' not in request.files:
                    return cls.build_generic_error("No file in request")

                upload = request.files['file']
                if upload.filename == "":
                    return cls.build_generic_error("No filename in request")

                repo_name = request.form.get('repo', None)
                logging.info("Target repo: %s", repo_name)

                if not repo_name:
                    return cls.build_generic_error(
                        "ERROR! 'repo' parameter not set!")

                if repo_name not in self._repos:
                    error_msg = ("ERROR! Target repo '{}' is invalid!"
                                 .format(repo_name))
                    return cls.build_generic_error(error_msg)

                repo_path = self._repos[repo_name]

                if not os.path.exists(repo_path):
                    logging.warning("Directory %s not present. Creating it...",
                                    repo_path)
                    try:
                        os.makedirs(repo_path)
                    except OSError as err:
                        if err.errno != errno.EEXIST:
                            raise

                (file_ptr, real_name) = tempfile.mkstemp(dir=self._tempdir)
                os.close(file_ptr)
                upload.save(real_name)

                task = ReceiveTask(upload.filename, real_name, repo_path,
                                   self._import_config)
                self._task_queue.add_task(task)

                logging.debug("/upload: POST request completed for %s",
                              upload.filename)

                return cls.build_response(200, "Importing bundle",
                                          task=task.get_id())
        elif request.method == "GET":
            logging.debug("/upload: GET request %s", request.full_path)
            return self._get_request_task(ReceiveTask)
        else:
            return cls.build_generic_error(
                "Only GET and POST methods supported")

    def push(self):
        """
        Extract a bundle from local repository and push to a remote
        """
        # Makes invocations of static methods shorter
        cls = self.__class__

        if not self._authenticator.authenticate(request):
            return cls.request_authentication()

        logging.debug(request.args)
        if request.method == 'PUT':
            try:
                ref = request.args['ref']
                remote = request.args['remote']
            except KeyError:
                return cls.build_generic_error(
                    "ref and remote arguments required")

            logging.debug("/push: %s to %s", ref, remote)
            if remote not in self._remote_push_adapter_map:
                return cls.build_generic_error(
                    "Remote is not in the whitelist")

            adapter = self._remote_push_adapter_map[remote]
            task = PushTask(ref, self._repo, ref, adapter, self._tempdir)
            self._task_queue.add_task(task)

            return cls.build_response(200,
                                      "Pushing {0} to {1}".format(ref, remote),
                                      task=task.get_id())

        elif request.method == "GET":
            logging.debug("/push: GET request %s", request.full_path)
            return self._get_request_task(PushTask)
        else:
            return cls.build_generic_error(
                "Only GET and PUT methods supported")

    @staticmethod
    def build_generic_error(message):
        logging.error(message)
        return UploadWebApp.build_response(400, message)

    @staticmethod
    def build_response(status_code, message, **kwargs):
        body = {
            'success': status.is_success(status_code),
            'message': message,
        }
        body.update(kwargs)
        return jsonify(body), status_code


class OstreeUploadServer(object):
    CONFIG_LOCATIONS = [
        '/etc/ostree/ostree-upload-server.conf',
        os.path.expanduser('~/.config/ostree/ostree-upload-server.conf'),
        'ostree-upload-server.conf',
    ]

    ADAPTER_IMPL_CLASSES = [DummyPushAdapter,
                            HttpPushAdapter,
                            ScpPushAdapter]

    def __init__(self, port, workers):
        self._port = port
        self._workers = workers

        self._adapters = {}
        for adapter_impl_class in OstreeUploadServer.ADAPTER_IMPL_CLASSES:
            self._adapters[adapter_impl_class.name] = adapter_impl_class

        self._remote_push_adapter_map = {}
        self._managed_repos = {}
        self._users = {}
        self._import_config = {}
        self._do_maintenance = True
        self.parse_config()

    def parse_config(self):
        config = ConfigParser(allow_no_value=True)
        config.read(OstreeUploadServer.CONFIG_LOCATIONS)

        for section in config.sections():
            if not section.startswith('remote-'):
                continue
            remote_dict = dict(config.items(section))
            remote_name = section.split('-')[1]
            adapter_type = remote_dict['type']
            if adapter_type in self._adapters:
                logging.debug("Setting up adapter %s, type %s", remote_name,
                              adapter_type)
                adapter_impl_class = self._adapters[adapter_type]
                self._remote_push_adapter_map[remote_name] = \
                    adapter_impl_class(remote_name, remote_dict)
            else:
                logging.error("Adapter %s: unknown type %s", remote_name,
                              adapter_type)

        # Enumerate all the allowed repos
        for section in config.sections():
            if not section.startswith('repo-'):
                continue

            repo_name = section[len('repo-'):]
            repo_definition = dict(config.items(section))
            repo_path = repo_definition['path']

            self._managed_repos[repo_name] = repo_path

            logging.info("Repo %s -> %s configuration added", repo_name,
                         repo_path)

        if not self._managed_repos:
            raise Exception('No repositories configured')

        if config.has_section('users'):
            self._users = dict(config.items('users'))

        if self._users:
            logging.debug("Users configured:")
            for user in sorted(self._users):
                logging.debug(" - %s", user)
        else:
            logging.warning("Warning! No authentication configured!")

        if config.has_section('import'):
            self._import_config = dict(config.items('import'))

        if self._import_config:
            logging.debug('Import configuration:')
            for key, value in sorted(self._import_config.items()):
                logging.debug('%s = %s', key, value)
        else:
            logging.warning('No import configuration!')

        if config.has_section('server'):
            self._do_maintenance = config.getboolean('server', 'maintenance')

    def perform_maintenance(self, workers, task_queue,
                            latest_maintenance_complete, latest_task_complete):
        time_since_maintenance = time() - latest_maintenance_complete
        time_since_task = time() - latest_task_complete[0]
        logging.debug("{:.1f} complete".format(time_since_task))

        maintenance_msg_format = ("{:.1f} since last task, {:.1f}/{} since "
                                  "last maintenance")
        logging.debug(maintenance_msg_format.format(time_since_task,
                                                    time_since_maintenance,
                                                    MAINTENANCE_WAIT))
        if time_since_maintenance > time_since_task:
            # Uploads have been processed since last maintenance
            logging.debug("Maintenance needed")
            if time_since_task >= MAINTENANCE_WAIT:
                logging.debug("Idle. Performing maintenance")
                workers.stop()

                repo_paths = list(self._managed_repos.values())
                logging.info("Performing maintenance on repos: %s", repo_paths)
                for active_repo in repo_paths:
                    logging.info("Performing maintenance on %s", active_repo)

                    if not os.path.isdir(active_repo):
                        logging.warning(
                            "Repo %s doesn't exist - skipping mainenance!",
                            active_repo)
                        continue

                    # Run flatpak build-update-repo
                    cmd = [
                        "flatpak",
                        "build-update-repo",
                        "--generate-static-deltas",
                        "--prune",
                    ]
                    gpg_homedir = self._import_config.get('gpg_homedir')
                    sign_key = self._import_config.get('sign_key')
                    if gpg_homedir:
                        cmd.append('--gpg-homedir=' + gpg_homedir)
                    if sign_key:
                        cmd.append('--gpg-sign=' + sign_key)
                    cmd.append(active_repo)

                    with RepoLock(active_repo, exclusive=True):
                        try:
                            output = check_output(cmd, stderr=STDOUT)
                            logging.info("Completed maintenance on %s: %s",
                                         active_repo, output)
                        except CalledProcessError as err:
                            logging.error("ERROR! Maintenance task failed!")
                            logging.error(err)

                workers.start(task_queue, self._workers)

                latest_maintenance_complete = time()

        return latest_maintenance_complete

    def run(self):
        # Array since we need to pass by ref
        last_task_complete = [time()]
        last_maintenance_complete = time()
        active_upload_counter = ThreadsafeCounter()

        task_queue = TaskQueue()

        logging.info("Starting server on %d...", self._port)

        logging.debug("Task completed callback %s", last_task_complete)

        def completed_callback(last_task_complete):
            logging.debug("Task completed callback %s", last_task_complete)
            last_task_complete[:] = [time()]

        workers = WorkerPoolExecutor(partial(completed_callback,
                                             last_task_complete))
        workers.start(task_queue, self._workers)

        webapp = UploadWebApp(__name__,
                              self._users,
                              self._managed_repos,
                              active_upload_counter,
                              self._remote_push_adapter_map,
                              self._import_config,
                              task_queue)

        http_server = WSGIServer(('', self._port), webapp)
        http_server.start()

        logging.info("Server started on %s", self._port)

        # loop until interrupted
        while True:
            try:
                gsleep(5)
                task_queue.join()
                logging.debug("Task queue empty, %s uploads ongoing",
                              str(active_upload_counter.count))

                # Continue looping if maintenance not desired
                if self._do_maintenance:
                    last_maintenance_complete = self.perform_maintenance(
                        workers, task_queue, last_maintenance_complete,
                        last_task_complete)
            except (KeyboardInterrupt, SystemExit):
                break

        logging.info("Cleaning up resources...")

        http_server.stop()

        workers.stop()


def main():
    parser = argparse.ArgumentParser()

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

    OstreeUploadServer(args.port, args.workers).run()


if __name__ == '__main__':
    main()
