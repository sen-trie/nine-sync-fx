# Wraps a node socket so the backends can tell a node-computed scalar from a plain float constant.
class MmeSock:
    __slots__ = ("socket",)

    def __init__(self, socket):
        self.socket = socket
