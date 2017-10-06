#!/bin/bash -e

CURRENT_DIR=$(dirname $0)

REPO_DIR="$CURRENT_DIR/repo"


if [ -d "$REPO_DIR" ]; then
  echo "Removing the stale repo directory at $REPO_DIR"
  rm -rf "$REPO_DIR"
fi

$CURRENT_DIR/../flatpak-import.py --debug "$REPO_DIR" "$CURRENT_DIR/hello.flatpak"
