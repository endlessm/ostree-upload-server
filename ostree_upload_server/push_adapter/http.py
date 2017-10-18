import logging
import os.path

import requests
from requests_toolbelt.multipart import MultipartEncoder

from ostree_upload_server.push_adapter.base import BasePushAdapter


class HttpPushAdapter(BasePushAdapter):
    name = "http"

    def __init__(self, name, settings):
        super(HttpPushAdapter, self).__init__(name)

        self._url = settings.get('url')
        username = settings.get('username')
        password = settings.get('password')

        if username and password:
            self._auth = requests.auth.HTTPBasicAuth(username, password)
        else:
            self._auth = None

    def push(self, bundle):
        logging.debug("Http push {0} to {1}".format(bundle, self._url))

        encoder = MultipartEncoder({'file': (os.path.basename(bundle),
                                             open(bundle, 'rb'),
                                             'application/octet-stream')})
        r = requests.post(self._url,
                          data=encoder,
                          auth=self._auth,
                          headers={'Content-Type': encoder.content_type})
        logging.debug("Http push {0} response: {1}".format(bundle, r.text))
        return r.status_code == requests.codes.ok
