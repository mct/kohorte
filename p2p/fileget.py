#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import socket
import errno
import traceback
import re
import random
import time
import os

import config

from eventloop import EventLoop
from messages import MessageTypes
from bencode import bencode, bdecode
from errors import *
from util import *

class FileGet(object):
    '''
    Object to handle a single 'file_get' Kohorte request.  Performs checks
    against directory traversals, and filters the files in the .git directory
    it will serve.
    '''

    def __repr__(self):
        try:
            return 'FileGet(%d)' % self.id_
        except:
            return 'FileGet(oops, %s)' % id(self)

    def __init__(self, peer, id_, filename):
        self.peer = peer
        self.id_ = id_
        self.chunk_size = config.file_get_chunk_size
        self.sent = 0
        self.ack = -1
        self.window = config.file_get_window
        self.eof = False
        self.fd = None
        self.closed = False

        filename = os.path.abspath(self.peer.swarm.directory + '/.git/' + filename)
        basedir =  os.path.abspath(self.peer.swarm.directory + '/.git/')
        self.check_filename(filename, basedir)

        self.fd = open(filename)

    def check_filename(self, filename, basedir):
        '''
        Raises an IOError if there's anything wrong with the requested filename
        '''

        # Is this sufficient to protect against directory traversal attacks?
        if not filename.startswith(basedir):
            raise IOError("Directory traversal attempt thwarted!  Requested %s" % filename)

        # Should we censor this file, to filter out remote refs?  Currently, it
        # leaks a list of other remotes the repository has defined, which may
        # contain sensitive information.
        if filename == basedir + '/info/refs':
            return True

        elif filename == basedir + '/HEAD':
            return True

        elif filename.startswith(basedir + '/objects/'):
            return True

        else:
            raise IOError('Rejecting filename: %s' % filename)

    def close(self):
        if self.closed:
            return
        self.closed = True

        #print timestamp(), self, "Closing"
        self.fd.close()

    def on_ack(self, ack):
        '''
        Called by Peer() for each 'ack' Message received from the remote peer.
        If the remote peer has acknowledged all of the outstanding chunks, send
        N more (specified by config.file_get_window).

        If all sent data has been acknowledged, and if we've reached the end of
        the file being served, returns True.  Otherwise, returns False.

        Raises an assert Exception if the ack number is out of bounds.
        '''

        assert self.ack < ack <= self.sent
        self.ack = ack

        # If the remote peer hasn't caught up to us yet...
        if self.ack < self.sent:
            return

        # If the remote peer has caught up, and we're at the end of the file
        if self.eof:
            #print timestamp(), self, "Send buffer empty"
            return True

        for i in range(self.window):
            buf = self.fd.read(self.chunk_size)
            self.sent += 1
            self.peer.send('file_dat', id=self.id_, chunk=self.sent, buf=buf)
            if buf == '':
                self.eof = True
                #print timestamp(), self, "EOF from file"
                break

        return False
