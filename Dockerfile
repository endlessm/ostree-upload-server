FROM ubuntu:16.04
MAINTAINER Endless Services Team <services@endlessm.com>

RUN apt-get update && \
    apt-get install -y software-properties-common

LABEL version="0.1"

RUN add-apt-repository ppa:alexlarsson/flatpak

RUN apt-get update && \
    apt-get dist-upgrade -y && \
    apt-get install -y apt-transport-https \
                       curl \
                       flatpak


# Keep in line with requirements.txt and setup.py
RUN apt-get update && apt-get install -y python \
                                         python-appdirs \
                                         python-click \
                                         python-flask \
                                         python-gevent \
                                         python-greenlet \
                                         python-itsdangerous \
                                         python-jinja2 \
                                         python-markupsafe \
                                         python-packaging \
                                         python-requests-toolbelt \
                                         python-six \
                                         python-werkzeug

EXPOSE 5000

ENV INSTALL_DIR="/opt/ostree-upload-server"

RUN mkdir -p $INSTALL_DIR
WORKDIR $INSTALL_DIR

# XXX: Use static/unique UID/GID to ensure consistency in mounted volume handling
RUN groupadd -r -g 800 ostree-server && \
    useradd -r -u 800 -g 800 ostree-server

ADD . $INSTALL_DIR
RUN chown -R ostree-server:ostree-server $INSTALL_DIR && \
    chmod +x $INSTALL_DIR/ostree-upload-server.py

RUN mkdir /repo && \
    chown -R ostree-server:ostree-server /repo

USER ostree-server

CMD ["/bin/sh", "-c", "${INSTALL_DIR}/ostree-upload-server.py /repo"]
