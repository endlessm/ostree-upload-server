# OstreeUploadServer tests
#
# Note that there are some funky issues with threads and processes that
# need to be handled here to allow the server and client to run
# concurrently in the test.
#
# The simple thing to do would be to start the server in a separate
# process so it doesn't block the client. It could be executed via
# subprocess, but that would lose pytest integration and require getting
# the bound port back over some channel. It could also be forked via
# multiprocessing or similar, but this breaks ostree pulls because glib
# loses track of threads when forked.
#
# To keep the server in the same process, grequests is used so that the
# client requests can run asynchronously via gevent and yield to the
# server as needed, which also uses gevent.

import grequests
import logging
from ostree_upload_server.server import OstreeUploadServer
from passlib.hash import pbkdf2_sha256
import pytest
import requests
from textwrap import dedent

from .util import BUNDLES, GPG_KEYS

logger = logging.getLogger(__name__)


@pytest.fixture
def server_conf(tmp_path, repo, repo_gpg_homedir):
    """Generate a config file for the server"""
    conf_args = {
        'gpg_homedir': str(repo_gpg_homedir),
        'keyring': str(GPG_KEYS['upload']['keyring']),
        'sign_key': GPG_KEYS['server']['id'],
        'repo_path': repo.get_path().get_path(),
        'password_hash': pbkdf2_sha256.hash('secret'),
    }
    conf = dedent('''\
    [import]
    gpg_homedir = {gpg_homedir}
    keyring = {keyring}
    sign_key = {sign_key}

    [repo-main]
    path = {repo_path}

    [users]
    user = {password_hash}
    '''.format(**conf_args))

    conf_path = tmp_path / 'ostree-upload-server.conf'
    with open(conf_path, 'w') as cf:
        cf.write(conf)

    return conf_path


@pytest.fixture
def server(server_conf):
    """Start the server and yield until the test completes"""
    server = OstreeUploadServer(0, 2, str(server_conf))
    server._start()
    yield server
    server._stop()


@pytest.mark.parametrize('bundle_type', ['flatpak', 'tar', 'tgz'])
def test_upload(bundle_type, server):
    port = server._http_server.server_port
    url = 'http://127.0.0.1:{}/upload'.format(port)

    with requests.Session() as session:
        session.auth = ('user', 'secret')

        # POST the bundle. This is form encoded with the bundle in the
        # file field and the repo name in the repo field.
        with open(BUNDLES[bundle_type], 'rb') as bundle:
            data = {'repo': 'main'}
            files = {'file': bundle}
            req = grequests.request('POST', url, session=session, data=data,
                                    files=files, timeout=5)
            resp = grequests.map([req])[0]
            resp.raise_for_status()

        # Get the task ID from the response
        task = resp.json()['task']

        # Loop until the task completes
        state = ''
        params = {'task': task}
        while state not in ('COMPLETED', 'FAILED'):
            req = grequests.request('GET', url, session=session,
                                    params=params, timeout=5)
            resp = grequests.map([req])[0]
            resp.raise_for_status()
            state = resp.json()['state']
            logger.info('Current state: %s', state)

        assert state == 'COMPLETED'
