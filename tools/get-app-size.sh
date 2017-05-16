#!/bin/sh

# set safer error and list handling
set -euo pipefail
IFS=$'\n\t'

repo=$1
ref=$2

size=$(ostree --repo="${repo}" ls -R "${ref}" | awk '{ sum+= $4 }; END { print sum }')

echo $ref: $size
