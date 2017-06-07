
class BasePushAdapter():
    def __init__(self):
        pass


class HTTPPushAdapter(BasePushAdapter):
    def __init__(self):
        pass


class SCPPushAdapter(BasePushAdapter):
    def __init__(self):
        pass



adapters = {
               'http': HTTPPushAdapter,
               'scp': SCPPushAdapter
           }

