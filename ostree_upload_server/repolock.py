# repolock.py - Endless OSTree repository locking
#
# Copyright (C) 2017  Endless Mobile, Inc <maintainers@endlessm.com>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

import errno
import fcntl

import logging
import os
import time

logger = logging.getLogger(__name__)


class EosOSTreeError(Exception):
    """Errors from the eosostree module"""
    def __init__(self, *args):
        self.msg = ' '.join(map(str, args))

    def __str__(self):
        return str(self.msg)


class RepoLock(object):
    # Repo lock file name. This intentionally chosen to be different
    # than the name used in the upstream locking work ($repo/lock) so
    # that deadlocks aren't introduced when that's landed and deployed.
    LOCK_FILE = '.eoslock'

    # Wait 30 minutes until locking timeout by default.
    LOCK_TIMEOUT = 30 * 60

    def __init__(self, repo_path, exclusive=False, timeout=LOCK_TIMEOUT):
        self._repo_path = repo_path
        self._exclusive = exclusive
        self._timeout = timeout
        self._lock_file = None

    def __enter__(self):
        """Context manager for lock()"""
        self._open()
        self._lock()

    def __exit__(self, *args):
        self._unlock()
        self._close()

    def _open(self):
        lock_path = os.path.join(self._repo_path, self.LOCK_FILE)
        logger.info('Opening lock file %s', lock_path)
        self._lock_file = open(lock_path, 'w')

    def _close(self):
        if self._lock_file is not None:
            self._lock_file.close()
            self._lock_file = None

    def __del__(self):
        self._close()

    def _lock(self):
        """Acquire a flock on the repository

        Takes a flock on the repository lock file. By default the lock
        is shared. An exclusive lock can be acquired by setting exclusive
        to True. The lock acquisition will block for timeout seconds.
        If timeout is None, the lock acquisition will block
        indefinitely.
        """
        lock_path = self._lock_file.name
        logger.info('Locking file %s %s', lock_path,
                    'exclusive' if self._exclusive else 'shared')
        mode = fcntl.LOCK_EX if self._exclusive else fcntl.LOCK_SH
        lock_fd = self._lock_file.fileno()
        if self._timeout is None:
            # Full blocking lock
            fcntl.flock(lock_fd, mode)
        else:
            # Try non-blocking lock and sleep until timeout exhausted
            mode |= fcntl.LOCK_NB
            wait = self._timeout
            while True:
                try:
                    fcntl.flock(lock_fd, mode)
                    break
                except IOError as err:
                    if err.errno != errno.EWOULDBLOCK:
                        raise

                # Fail if the timeout has been reached
                if wait <= 0:
                    raise EosOSTreeError('Could not lock', lock_path,
                                         'in', self._timeout, 'seconds')

                # Try again in 1 second
                if wait % 30 == 0:
                    logger.debug('Could not acquire lock %s, %d second%s '
                                 'until timeout', lock_path, wait,
                                 's' if wait > 1 else '')
                wait -= 1
                time.sleep(1)

    def _unlock(self):
        """Remove the repository flock"""
        logger.info('Unlocking file %s', self._lock_file.name)
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
