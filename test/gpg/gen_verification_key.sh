#!/bin/bash -e

CURRENT_DIR=$(dirname $0)

GPG_HOMEDIR="$CURRENT_DIR/verifying"
rm -rf "$GPG_HOMEDIR"
mkdir "$GPG_HOMEDIR"

gpg2 --homedir="$GPG_HOMEDIR" --batch --gen-key verifying.conf

# Hack up a valid gpg v1 key
# https://superuser.com/questions/655246/are-gnupg-1-and-gnupg-2-compatible-with-each-other

SIGNING_KEY=$(gpg2 --homedir $GPG_HOMEDIR --list-secret-keys | \
              grep -A1 ^sec | \
              tail -1 |  \
              tr -d '[:space:]')

echo "Found key: $SIGNING_KEY"

gpg2 --homedir $GPG_HOMEDIR \
     --armor \
     --export-secret-key $SIGNING_KEY > verifying.asc.key

gpg1 --homedir $GPG_HOMEDIR \
     --import verifying.asc.key

rm verifying.asc.key
