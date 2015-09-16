#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import socket
import time

from eventloop import EventLoop
from swarm import Swarm
from peer import Peer
from errors import *
from util import *

class Listener(object):
    '''
    TCP listener.  On accept() instantiates a new Peer().  Initially that Peer
    won't know what swarm it will be part of until it receives the first 'helo'
    Message.
    '''

    def __repr__(self):
        try:
            return "Listener" + repr(self.addr)
        except:
            return "Listener(oops, %s)" % id(self)

    def __init__(self, addr, backlog=5):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(addr)
        sock.listen(backlog)
        sock.setblocking(False)

        self.sock = sock
        self.addr = sock.getsockname()

        EventLoop.register(self)
        print timestamp(), self, "Listening"

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        raise Exception("Something terrbile has happened, listener was asked to close")

    def wants_readable(self):
        return True

    def on_readable(self):
        ret = self.sock.accept()
        if ret is None:
            return
        sock, addr = ret

        print timestamp(), self, "Incoming connection from", addr
        peer = Peer(addr, None, sock)
