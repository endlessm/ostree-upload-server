#!/bin/bash

# set safer error and list handling
set -euo pipefail
IFS=$'\n\t'

repo="$1"
ref="$2"

tmp="${ref%/*/*/*}"
IFS=/ read app arch ver <<< "${ref#"$tmp"/}"

# echo app: $app
# echo arch: $arch
# echo ver: $ver

ofname="${app}-${arch}-${ver}.flatpak"
flatpak build-bundle "${repo}" "${ofname}" "${ref}"
