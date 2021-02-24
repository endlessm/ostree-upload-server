from pathlib import Path

TESTDIR = Path(__file__).parent

# Test bundles
BUNDLES = {
    'flatpak': TESTDIR / 'hello.flatpak',
    'tar': TESTDIR / 'hello.tar',
    'tgz': TESTDIR / 'hello.tgz',
}

# GPG key paths and IDs
GPG_KEYS = {
    'upload': {
        'id': 'EAEC9B60606A9D450D4B90365B6A1DE7198A9FE2',
        'private': TESTDIR / 'gpg/upload-private.pem',
        'public': TESTDIR / 'gpg/upload-public.pem',
        'keyring': TESTDIR / 'gpg/upload-public.gpg',
    },
    'server': {
        'id': '3244F80F5A649CFD350C86BBF6CAF3E6BC15996C',
        'private': TESTDIR / 'gpg/server-private.pem',
        'public': TESTDIR / 'gpg/server-public.pem',
    }
}
