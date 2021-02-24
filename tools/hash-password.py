#!/usr/bin/env python3

# Generate an encrypted password for use in ostree-upload-server.conf
# using PBKDF2-SHA256

from getpass import getpass
from passlib.hash import pbkdf2_sha256

password = getpass()
print(pbkdf2_sha256.encrypt(password))
