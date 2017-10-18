import logging

from passlib.hash import pbkdf2_sha256


class Authenticator(object):
    def __init__(self, users):
        self._users = users

    def authenticate(self, request):
        if not self._users:
            return True

        auth = request.authorization

        if not auth:
            return False

        if auth.username not in self._users:
            return False

        # Check the pbkdf2-sha256 encrypted password
        hashed_password = self._users[auth.username]
        if not pbkdf2_sha256.identify(hashed_password):
            logging.warning('Hashed password for user {} is not '
                            'valid for pbkdf2-sha256 algorithm'
                            .format(auth.username))
            return False

        if not pbkdf2_sha256.verify(auth.password, hashed_password):
            return False

        return True
