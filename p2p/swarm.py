#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import time
import os
import base64

import lpd
import peer
import config

from git import Git
from errors import *
from util import *
from peer import Peer
from eventloop import EventLoop

class Swarm(object):
    '''
    Represents a single Swarm.  Maintains a list of Peers associated with it, a
    list of outbound dampened addresses, state about whether the repository is
    currently being cloned, and other miscellaneous bookkeeping.

    Registers itself with the EventLoop only to receive periodic heartbeats, to
    clean up dampened peers.
    '''

    index = {}

    @classmethod
    def get(cls, sha):
        return cls.index.get(sha, None)

    @classmethod
    def list(cls):
        return sorted(list(cls.index.values()))

    def __repr__(self):
        try:
            return "Swarm(%s)" % repr(self.short_sha)
        except:
            return "Swarm(oops, %s)" % id(self)

    def __init__(self, directory, sha=None, clone=False):
        self.directory = os.path.abspath(directory)
        self.clone = clone
        self.peers = []
        self.dampen = {}
        self.aka = {} # mapping of addr's to peer-ids
        self.loops = {}
        self.closed = False

        if self.clone:
            self.git = None
            self.sha = sha
        else:
            self.git = Git(self.directory)
            self.sha = self.git.root

        self.short_sha = self.sha[:7]

        if self.index.has_key(self.sha):
            raise Exception("Swarm already exists for %s" % self.directory)

        self.index[self.sha] = self
        EventLoop.register(self)

        print timestamp(), self, "Registered", self.sha
        lpd.LPD.update()

    def on_heartbeat(self):
        assert not self.closed

        now = time.time()
        for addr, expires in self.dampen.items():
            if expires <= now:
                print timestamp(), self, "Undampening", addr
                del self.dampen[addr]

        for addr,peerid in self.aka.items():
            if peerid not in [ x.remote_peerid for x in self.peers ]:
                print timestamp(), self, "Removing known alias", addr, peerid
                del self.aka[addr]

    def add_peer(self, peer):
        assert peer not in self.peers
        self.peers.append(peer)

        if not peer.inbound:
            self.dampen[peer.connection.addr] = time.time() + config.dampen_time

    def remove_peer(self, peer):
        assert peer in self.peers
        self.peers.remove(peer)

    def clone_done(self):
        self.git = Git(self.directory)
        self.clone = False
        for x in self.peers:
            x.cloning = False

    def drop(self):
        if self.closed:
            return
        self.closed = True

        del self.index[self.sha]
        EventLoop.unregister(self)

        print timestamp(), self, "Dropping (and %d peers)" % len(self.peers)

        # We need to make a copy of self.peers, because when peers are closed,
        # they'll be modifying the list as we iterate through it.  This was a
        # an *extremely* annoying bug to track down.
        for x in list(self.peers):
            print "Closing", x
            x.close()

    def connect(self, addr, peerid=None):
        if peerid and peerid in [ x.remote_peerid for x in self.peers ]:
            print timestamp(), self, "Already have connection to %s, ignoring" % repr(peerid)
            return False

        if peerid and peerid in [ x.my_peerid for x in self.peers ]:
            print timestamp(), self, "Not going to connect to myself"
            return False

        if self.dampen.has_key(addr):
            print timestamp(), self, "Not connecting to %s, peer is dampened" % repr(addr)
            return False

        if self.aka.has_key(addr):
            if self.aka[addr] in [ x.remote_peerid for x in self.peers ]:
                print timestamp(), self, "Not connection to %s, known to be %s, already connected" % (repr(addr), repr(self.aka[addr]))
                return
        
        if self.loops.has_key(addr):
            print timestamp(), self, "Known to be a loopback address, not connecting to", addr
            return

        return Peer(addr, self)
