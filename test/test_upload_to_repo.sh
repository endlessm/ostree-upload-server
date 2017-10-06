#!/bin/bash -e

CURRENT_DIR=$(dirname $0)

echo "Uploading file..."
output=$(curl -s -F file=@$CURRENT_DIR/hello.flatpak -u user:secret http://localhost:5000/upload)

task_id=$(echo "$output" | jq -r '.task')
echo "File uploaded. Task ID is $task_id."

echo
echo "Checking result of upload..."

curl -u user:secret http://localhost:5000/upload?task=${task_id}

echo
echo "Completed uploading the test file"
