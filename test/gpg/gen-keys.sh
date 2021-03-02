#!/bin/bash -e

CURRENT_DIR=$(dirname $0)

UPLOAD_GPGDIR="$CURRENT_DIR/upload"
SERVER_GPGDIR="$CURRENT_DIR/server"

rm -rf "$UPLOAD_GPGDIR"
mkdir -p -m700 "$UPLOAD_GPGDIR"
gpg --batch --homedir "$UPLOAD_GPGDIR" --gen-key "$CURRENT_DIR/upload.conf"
gpg --batch --homedir "$UPLOAD_GPGDIR" --export-secret-keys --armor \
    > "$CURRENT_DIR/upload-private.pem"
gpg --batch --homedir "$UPLOAD_GPGDIR" --export --armor \
    > "$CURRENT_DIR/upload-public.pem"
gpg --batch --homedir "$UPLOAD_GPGDIR" --export \
    > "$CURRENT_DIR/upload-public.gpg"
gpg-connect-agent --homedir "$UPLOAD_GPGDIR" killagent /bye

rm -rf "$SERVER_GPGDIR"
mkdir -p -m700 "$SERVER_GPGDIR"
gpg --batch --homedir "$SERVER_GPGDIR" --gen-key "$CURRENT_DIR/server.conf"
gpg --batch --homedir "$SERVER_GPGDIR" --export-secret-keys --armor \
    > "$CURRENT_DIR/server-private.pem"
gpg --batch --homedir "$SERVER_GPGDIR" --export --armor \
    > "$CURRENT_DIR/server-public.pem"
gpg-connect-agent --homedir "$SERVER_GPGDIR" killagent /bye
