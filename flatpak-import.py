#!/usr/bin/env python2

import logging
import os

from argparse import ArgumentParser

from ostree_upload_server.flatpak_importer import FlatpakImporter

if __name__ == "__main__":
    parser = ArgumentParser(description='Import flatpak into a local repository')
    parser.add_argument('repo', help='repository name to use')
    parser.add_argument('flatpak', help='file to import')
    parser.add_argument('-g', '--gpg-homedir',
                         help='GPG homedir to use when looking for keyrings')
    parser.add_argument('-k', '--keyring', help='additional trusted keyring file')
    parser.add_argument('-s', '--sign-key', help='GPG key ID to sign the commit with')
    parser.add_argument('-v', '--verbose', action='store_true',
                         help='verbose log output')
    parser.add_argument('-d', '--debug', action='store_true', help='debug log output')

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    elif args.verbose:
        logging.basicConfig(level=logging.INFO)
    else:
        logging.basicConfig(level=logging.WARNING)

    FlatpakImporter.import_flatpak(args.flatpak,
                                   args.repo,
                                   args.gpg_homedir,
                                   args.keyring,
                                   args.sign_key)
