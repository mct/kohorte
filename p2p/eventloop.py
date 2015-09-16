#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import traceback
import time
import select

class EventLoop(object):
    '''
    A single-threaded, non-blocking event loop.  Objects register themselves
    with the EventLoop by calling the 'register' class method.  An object can
    implement the following methods to receive callbacks:

        - wants_readable:  Returns True to be included in the readable select() list.
        - wants_writable:  Returns True to be included in the writable select() list.
        - on_readable: Called when select() reports the object as readable
        - on_writable: Called when select() reports the object as writable
        - on_heartbeat: Called roughly once every one to two seconds
        - fileno: Used by select() to return a file descriptor
        - close: Called on error

    If a callback method generates an Exception, the object's 'close' method is
    called, and it is removed from the event loop.

    Very clean, very simple.
    '''

    socks = []
    started = False

    def __init__(self, heartbeat_time=1):
        self.heartbeat_time = heartbeat_time
        self.last_heartbeat = 0

    @classmethod
    def list(self):
        return list(self.socks)

    @classmethod
    def register(self, x):
        if x not in self.socks:
            self.socks.append(x)

    @classmethod
    def unregister(self, x):
        if x in self.socks:
            self.socks.remove(x)

    ##

    def attempt(self, x, f):
        '''
        Calls an object's callback method, dealing with Exceptions as
        necessary.
        '''

        try:
            f()
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc()
            print
        else:
            return

        try:
            x.close()
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc()
            print

        self.unregister(x)
        print "Uncaught exception, removed from EventLoop:", x

    def do_heartbeat(self, force=False):
        if not force and time.time() - self.last_heartbeat < self.heartbeat_time:
            return

        self.last_heartbeat = time.time()

        for x in self.socks:
            f = getattr(x, 'on_heartbeat', None)
            if f:
                self.attempt(x, x.on_heartbeat)

    def loop(self):
        '''
        Loop forever
        '''

        self.__class__.started = True
        last_socks = []

        while True:

            # Force a call to the heartbeat functions if the list of objects
            # has changed, so that objects interacting with each other can
            # perform any cleanup that may be required if one goes away.
            if last_socks != self.socks:
                last_socks = list(self.socks)
                self.do_heartbeat(force=True)
            else:
                self.do_heartbeat()

            rlist = [ x for x in self.socks if hasattr(x, 'wants_readable') and x.wants_readable() ]
            wlist = [ x for x in self.socks if hasattr(x, 'wants_writable') and x.wants_writable() ]
            xlist = []

            rlist, wlist, xlist = select.select(rlist, wlist, xlist, self.heartbeat_time)

            for x in rlist:
                self.attempt(x, x.on_readable)

            # If during on_readable() an object unreigstered itself, don't call it again for on_writable.
            wlist = [ x for x in wlist if x in self.socks ]

            for x in wlist:
                self.attempt(x, x.on_writable)
