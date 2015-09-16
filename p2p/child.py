#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import fcntl
import traceback
import os
import signal

from subprocess import Popen, PIPE, STDOUT

from eventloop import EventLoop
from errors import *
from util import *

class Child(object):
    '''
    fork/execs a single child, adds itself to the EventLoop to read from
    stdout, waits for that process to exit.
    '''

    def __repr__(self):
        try:
            return "Child(%s, %d)" % (self.tag, self.pid)
        except:
            return "Child(oops, %s)" % id(self)

    def __init__(self, peer, tag, cmd):
        self.tag = tag
        self.peer = peer
        self.cmd = cmd

        self.popen = Popen(cmd, stdout=PIPE, stderr=STDOUT, preexec_fn=os.setsid)
        self.pid = self.popen.pid
        self.fd = self.popen.stdout
        self.eof = False
        self.closed = False

        # Set non-blocking
        flags = fcntl.fcntl(self.fd, fcntl.F_GETFL)
        flags |= os.O_NONBLOCK
        fcntl.fcntl(self.fd, fcntl.F_SETFL, flags)

        print timestamp(), self, "Running", repr(' '.join(cmd))
        EventLoop.register(self)

    def fileno(self):
        return self.fd.fileno()

    def close(self):
        if self.closed:
            return
        self.closed = True

        print timestamp(), self, "I was asked to close?  Ok..."
        EventLoop.unregister(self)

        try:
            self.fd.close()
            os.killpg(self.pid, signal.SIGTERM)
            self.popen.wait()
        except Exception:
            traceback.print_exc()
            print

    def on_heartbeat(self):
        if self.peer.closed:
            print timestamp(), self, "Peer is gone? Closing"
            self.close()

    def wants_readable(self):
        if not self.closed:
            return True

    def on_readable(self):
        buf = self.fd.read(1024)

        if buf:
            for line in buf.split('\n'):
                if line == '':
                    continue
                print timestamp(), self, repr(line.rstrip())
            return

        #print timestamp(), self, "EOF"

        # If we waitpid() with os.WNOHANG, sometimes our waitpid() syscall will
        # execute before our child process has had a chance to exit(), in which
        # case it returns the PID as 0.  As we can be reasonably assured that
        # the child will exit soon now that it has closed sdout, let's risk
        # blocking.

        #(pid, exitcode) = os.waitpid(self.pid, os.WNOHANG)
        (pid, exitcode) = os.waitpid(self.pid, 0)
        assert pid == self.pid

        print timestamp(), self, "exit", exitcode
        self.exitcode = exitcode
        self.closed = True
        EventLoop.unregister(self)
