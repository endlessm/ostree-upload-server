# pytest fixtures
# https://docs.pytest.org/en/stable/fixture.html

import gi
from gi.repository import Gio
import pytest
import subprocess

from .util import GPG_KEYS

gi.require_version('OSTree', '1.0')
from gi.repository import OSTree  # noqa: E402


@pytest.fixture
def repo(tmp_path):
    repodir = tmp_path / 'repo'
    repodir.mkdir()

    repo = OSTree.Repo.new(Gio.File.new_for_path(str(repodir)))
    repo.create(OSTree.RepoMode.ARCHIVE_Z2)

    return repo


@pytest.fixture
def repo_gpg_homedir(tmp_path):
    homedir = tmp_path / 'gnupg'
    homedir.mkdir(mode=0o700)

    # Import the repo private key
    cmd = ('gpg', '--batch', '--homedir', str(homedir),
           '--import', str(GPG_KEYS['server']['private']))
    subprocess.run(cmd, check=True)

    yield homedir

    # Cleanup. With gpg >= 2.2.18 this isn't necessary, but just be nice
    # about it.
    cmd = ('gpg-connect-agent', '--no-autostart', '--homedir', str(homedir),
           'killagent', '/bye')
    subprocess.run(cmd, check=True)
