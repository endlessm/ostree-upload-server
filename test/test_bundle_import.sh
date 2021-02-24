#!/bin/bash -e

CURRENT_DIR=$(dirname $0)

REPO_DIR="$CURRENT_DIR/repo"

if [ $# -ne 1 ]; then
  echo "Usage: $0 < flatpak | tgz | tar >"
  exit 1
fi

BUNDLE_TYPE="$1"
BUNDLE_FILE="$CURRENT_DIR/hello.$BUNDLE_TYPE"

if [ ! -f "$BUNDLE_FILE" ]; then
  echo "ERROR! File does not exist!"
  exit 1
fi

if [ -d "$REPO_DIR" ]; then
  echo "Removing the stale repo directory at $REPO_DIR"
  rm -rf "$REPO_DIR"
fi

$CURRENT_DIR/../bundle-import.py --debug \
    --gpg-homedir "$CURRENT_DIR/gpg/server" \
    --keyring "$CURRENT_DIR/gpg/upload-public.gpg" \
    --sign-key server@example.com \
    "$REPO_DIR" "$BUNDLE_FILE"
