import logging

from ostree_upload_server.push_adapter.base import BasePushAdapter


class DummyPushAdapter(BasePushAdapter):
    name = "dummy"

    def __init__(self, name, settings):
        super(DummyPushAdapter, self).__init__(name)

        logging.debug("Initialized dummy adapter")
        logging.debug(repr(settings))

    def push(self, bundle):
        logging.debug("Dummy push {0}".format(bundle))

        return True
