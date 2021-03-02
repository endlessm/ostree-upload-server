import inspect
import logging
import magic

from .importers.flatpak import FlatpakImporter
from .importers.tar import TarImporter, TgzImporter


class BundleImporter(object):
    BUNDLE_IMPORTERS = [FlatpakImporter,
                        TarImporter,
                        TgzImporter]

    @staticmethod
    def import_bundle(bundle, repository, gpg_homedir=None, keyring=None,
                      sign_key=None):
        logging.info("Starting the bundle import process...")
        for arg in inspect.getfullargspec(BundleImporter.import_bundle)[0]:
            logging.info("Set %s = '%s'", arg, locals()[arg])

        # Find the appropriate importer based on mimetype
        mime_type = magic.from_file(bundle, mime=True)

        importer_class = next(
            filter(lambda ext: mime_type == ext.MIME_TYPE,
                   BundleImporter.BUNDLE_IMPORTERS),
            None)
        if not importer_class:
            logging.error('ERROR! Unknown mime-type %s detected in %s',
                          mime_type, bundle)
            raise RuntimeError('Unknown mime-type {} in file {}'
                               .format(mime_type, bundle))

        # Instantiate the importer and run it
        importer = importer_class(bundle, repository, gpg_homedir, keyring,
                                  sign_key)
        importer.import_to_repo()
