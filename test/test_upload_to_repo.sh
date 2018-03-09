#!/bin/bash -e

CURRENT_DIR=$(dirname $0)
TARGET_REPO="$1"

if [ $# -lt 1 ]; then
  echo "Usage: $0 <repo_name>"
  exit 1
fi

test_file=$CURRENT_DIR/hello.flatpak
if [ $2 == 'tgz' ]; then
    echo "Testing tgz..."
    test_file=$CURRENT_DIR/hello.tgz
fi

echo "Uploading file (repo=\"$TARGET_REPO\")..."
output=$(curl -s -F file=@$test_file -F "repo=$TARGET_REPO" -u user:secret http://localhost:5000/upload)

task_id=$(echo "$output" | jq -r '.task')
echo "File uploaded. Task ID is $task_id."

echo
echo "Checking result of upload..."

curl -u user:secret http://localhost:5000/upload?task=${task_id}

echo
echo "Completed uploading the test file"
