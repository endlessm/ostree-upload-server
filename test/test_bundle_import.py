from ostree_upload_server.bundle_importer import BundleImporter
import pytest

from .util import BUNDLES, GPG_KEYS


@pytest.mark.parametrize('bundle_type', ['flatpak', 'tar', 'tgz'])
def test_import(bundle_type, repo, repo_gpg_homedir):
    BundleImporter.import_bundle(str(BUNDLES[bundle_type]),
                                 repo.get_path().get_path(),
                                 str(repo_gpg_homedir),
                                 str(GPG_KEYS['upload']['keyring']),
                                 GPG_KEYS['server']['id'])
