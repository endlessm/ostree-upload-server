Build a docker image:

  # docker build -t ostree-upload-server .


Check configuration in remotes.conf and flatpak-import.conf


Launch container:

  # docker run -it -p 127.0.0.1:5000:5000 ostree-upload-server


To upload a file with curl:

  # curl -F "file=@/path/to/app.bundle" http://localhost:5000/upload

