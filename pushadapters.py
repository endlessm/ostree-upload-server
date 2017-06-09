import logging


class BasePushAdapter(object):
    def __init__(self, name):
        self._name = name

    def __str__(self):
        return('PushAdapter({0})'.format(self._name))


class DummyPushAdapter(BasePushAdapter):
    def __init__(self, name, settings):
        super(DummyPushAdapter, self).__init__(name)
        logging.debug("initialized dummy adapter")
        logging.debug(repr(settings))

    def push(self, bundle):
        logging.debug("dummy push {0}".format(bundle))
        return True


class HTTPPushAdapter(BasePushAdapter):
    def __init__(self, name, settings):
        super(HTTPPushAdapter, self).__init__(name)
        self._url = settings.get('url')

    def push(self, bundle):
        logging.debug("http push {0} to {1}".format(bundle, self._url))
        return True


class SCPPushAdapter(BasePushAdapter):
    def __init__(self, name, settings):
        super(SCPPushAdapter, self).__init__(name)
        self._url = settings.get('url')

    def push(self, bundle):
        logging.debug("scp push {0} to {1}".format(bundle,
                                                   self._url))
        logging.error("scp push not yet implemented")
        return False


adapter_types = {
                    'dummy': DummyPushAdapter,
                    'http': HTTPPushAdapter,
                    'scp': SCPPushAdapter
                }
