Build a docker image:

  # docker build -t ostree-upload-server .


Check configuration in remotes.conf and flatpak-import.conf


Launch container:

  # docker run -it --rm \
      -p 127.0.0.1:5000:5000 \
      -v /tmp/ostree-upload-server.conf:/opt/ostree-upload-server/ostree-upload-server.conf \
      -v /tmp/flatpak-import.conf:/opt/ostree-upload-server/flatpak-import.conf \
      -v /tmp/eos-flatpak-keyring.gpg:/gpg/trusted-keys.gpg \
      -v /tmp/repo:/repo \
      ostree-upload-server

To upload a file with curl:

  # curl -F "file=@/path/to/app.bundle" -u user:secret http://localhost:5000/upload

Note the task ID in the returned JSON. Now poll the task:

  # curl -u user:secret "http://localhost:5000/upload?task=$TASK_ID"

Note the state in the returned JSON. When the state is COMPLETED or FAILED,
the task has completed.
