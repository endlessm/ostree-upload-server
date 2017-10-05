from abc import ABCMeta, abstractmethod

class BasePushAdapter():
    __metaclass__ = ABCMeta

    def __init__(self, name):
        self._name = name

    def __str__(self):
        return 'PushAdapter({0})'.format(self._name)

    @abstractmethod
    def push(self, bundle):
        raise Error('Cannot invoke BasePushAdapter.push() method!')
