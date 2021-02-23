import inspect
import logging
import os

import magic

from configparser import ConfigParser

from .importers.flatpak import FlatpakImporter
from .importers.tar import TarImporter, TgzImporter


class BundleImporter(object):
    CONFIG_PATHS = ['/etc/ostree/flatpak-import.conf',
                    os.path.expanduser('~/.config/ostree/flatpak-import.conf'),
                    'flatpak-import.conf']

    BUNDLE_IMPORTERS = [FlatpakImporter,
                        TarImporter,
                        TgzImporter]

    @staticmethod
    def _parse_config():
        config = ConfigParser()
        config.read(BundleImporter.CONFIG_PATHS)

        configs = {}
        if config.has_section('defaults'):
            configs = dict(config.items('defaults'))

        return configs

    @staticmethod
    def import_bundle(bundle, repository, gpg_homedir=None, keyring=None,
                      sign_key=None):

        # Grab configuration details
        defaults = BundleImporter._parse_config()

        # If we only have defaults, use those instead
        gpg_homedir = gpg_homedir or defaults.get('gpg_homedir', None)
        keyring = keyring or defaults.get('keyring', None)
        sign_key = sign_key or defaults.get('sign_key', None)

        logging.info("Starting the bundle import process...")
        for arg in inspect.getargspec(BundleImporter.import_bundle)[0]:
            logging.info("Set %s = '%s'", arg, locals()[arg])

        # Find the appropriate importer based on mimetype
        mime_type = magic.from_file(bundle, mime=True)

        importer_class = filter(lambda ext: mime_type == ext.MIME_TYPE,
                                BundleImporter.BUNDLE_IMPORTERS)[0]

        if not importer_class:
            logging.error('ERROR! Unknown mime-type detected in %s', bundle)
            raise RuntimeError('Unknown mime-type in file: {}'.format(bundle))

        # Instantiate the importer and run it
        importer = importer_class(bundle, repository, gpg_homedir, keyring,
                                  sign_key)
        importer.import_to_repo()
