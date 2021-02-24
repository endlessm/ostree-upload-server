import logging

from ostree_upload_server.push_adapter.base import BasePushAdapter


class ScpPushAdapter(BasePushAdapter):
    name = "scp"

    def __init__(self, name, settings):
        super(ScpPushAdapter, self).__init__(name)
        self._url = settings.get('url')

    def push(self, bundle):
        logging.debug("Scp push {0} to {1}".format(bundle,
                                                   self._url))
        logging.error("Scp push not yet implemented")
        return False
