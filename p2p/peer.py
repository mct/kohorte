#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import time
import traceback
import os
import base64
import socket

import connection
import listener
import config

from eventloop import EventLoop
from connection import PeerConnection
from fileget import FileGet
from proxy import ProxyListener
from child import Child

from errors import *
from util import *

## Import trouble, because peer.py and swarm.py import reach other?
#import swarm
#swarm = swarm.Swarm
#from swarm import Swarm
import swarm

class Peer(object):
    '''
    Encapsulates a single peer.  Uses PeerConnection to handle the TCP socket, and
    to encode/decode messages.  When a Message is received, PeerConnection calls
    the appropriate Peer callback method.  e.g., "ping" calls "on_ping".  For an
    outbound connection, it will also call "on_connect" when a connection has been
    established.
    '''

    my_peerid = None
    index = []
    pex = True

    @classmethod
    def list(cls):
        return list(cls.index)

    def __repr_peerid__(self):
        #return '%-10s' % self.remote_peerid
        return self.remote_peerid

    def __repr__(self):
        try:
            addr = "%s:%d" % self.connection.addr
            if self.swarm:
                sha = self.swarm.short_sha
            else:
                sha = 'Inbound'
            peerid = self.__repr_peerid__()
            return "Peer(%s, %s, %s)" % (repr(peerid), repr(sha), addr)
        except:
            return "Peer(oops, %s)" % id(self)

    def __init__(self, addr, swarm=None, sock=None):
        assert self.my_peerid

        if len(self.index) >= config.max_peers:
            raise Exception('Too many peers')

        if sock:
            assert swarm is None
            self.inbound = True
            self.connected = True

        else:
            assert swarm
            self.inbound = False
            self.connected = False

        self.swarm = swarm
        self.remote_peerid = None
        self.child = None
        self.proxy = None
        self.closed = False
        self.refs_sig = ''
        self.last_refs_check = 0
        self.negotiated = False
        self.cmd = ''
        self.cloning = False

        # dict of files we're fetching from remote peer (and serving via our http proxy)
        self.requests = {}
        self.next_request_id = 0

        # dict of files we're sending to remote peer
        self.sending = {}

        self.connection = PeerConnection(self, sock, addr)

        self.index.append(self)
        if self.swarm:
            self.swarm.add_peer(self)

        #print timestamp(), self, "Registered"

        if self.swarm and self.swarm.clone:
            print timestamp(), self, "Peer is a candidate for cloning"

    def close(self):
        if self.closed:
            return
        self.closed = True

        print timestamp(), self, "Closing Peer"

        if self.swarm:
            self.swarm.remove_peer(self)

        if self.proxy:
            self.proxy.close()

        if self.child:
            self.child.close()

        for x in self.sending.values():
            x.close()

        for x in self.requests.values():
            x.close()

        if self in self.index:
            self.index.remove(self)
        else:
            print self, timestamp(), "I wasn't in the index?"

        self.connection.close()

    def on_heartbeat(self):

        # Decide if this peer will take responsibility for cloning
        peers_cloning = [ x for x in self.list() if x.cloning ]
        if self.negotiated and self.swarm.clone and not peers_cloning:
            assert not self.child
            print timestamp(), self, "Cloning"
            self.cloning = True
            self.do_clone()

        # Monitor children
        if self.child and self.child.closed:
            should_merge = False

            # Cloning
            if self.cloning:
                if self.child.exitcode != 0:
                    print timestamp(), self, "ERROR: git clone failed."
                    self.close()
                    return
                else:
                    print timestamp(), self, "Clone done"
                    self.swarm.clone_done()
                    self.check_refs(update_only=True)

            # Fetching
            elif self.cmd == 'fetch':
                if self.child.exitcode != 0:
                    print timestamp(), self, "Fetch failed! Returned", self.child.exitcode
                elif config.automerge:
                    print timestamp(), self, "Fetch done, now Merging"
                    should_merge = True
                else:
                    print timestamp(), self, "Fetch done"

            # Merging
            elif self.cmd == 'merge':
                if self.child.exitcode != 0:
                    print timestamp(), self, "Merge Failed! Returned", self.child.exitcode
                else:
                    print timestamp(), self, "Merge done"

            # Wut?
            else:
                print timestamp(), self, "How did we get here?  Forgetting child", self.child

            self.proxy.close()
            self.child = None
            self.proxy = None

            if should_merge:
                self.do_merge()

        if self.connection.closed:
            print timestamp(), self, "Our connection closed?"
            self.close()

        self.check_refs()

    def send(self, *args, **kwargs):
        self.connection.send_msg(*args, **kwargs)

    def other_peers(self):
        '''
        A list of other peers in our swarm
        '''
        return [ x for x in self.swarm.peers if x != self ]

    def on_ping(self, msg):
        self.send('pong')

    def on_pong(self, msg):
        pass
    
    def send_helo(self):
        l = [ x for x in EventLoop.list() if isinstance(x, listener.Listener) ]
        assert len(l) == 1
        port = l[0].addr[1]

        self.send('helo',
                protocol='p2p-git',
                major=config.major_version,
                minor=config.minor_version,
                swarmid=self.swarm.sha,
                peerid=self.my_peerid,
                port=port,
                client='mainline-' + str(config.version),
            )

    def on_connect(self):
        self.connected = True
        self.send_helo()

    def on_helo(self, msg):
        self.remote_peerid = msg['peerid']
        self.advertised_port = msg['port']

        if self.inbound:
            sha = msg['swarmid']
            self.swarm = swarm.Swarm.get(sha)

            if not self.swarm:
                print timestamp(), self, "Unknown swarm", sha
                self.close()
                return

            self.swarm.add_peer(self)
            self.send_helo()

        # The loopback test is deliberely placed after the swarm check, so even
        # if we know it's a loopback, we can still send a HELO before closing,
        # so the *remote* side can know it's a loopback, too.  That lets us
        # record the outgoing address that was used in our list of loopback
        # addresses, so we don't make the same mistake again in the future.
        if self.my_peerid == self.remote_peerid:
            print timestamp(), self, "Loopback peer detected"
            if not self.inbound:
                self.swarm.loops[self.connection.addr] = time.time()
            self.close()
            return

        if not self.inbound:
            self.swarm.aka[self.connection.addr] = self.remote_peerid

        # Search for existing connection to this remote_peerid
        if self.remote_peerid in [ x.remote_peerid for x in self.other_peers() ]:
            print timestamp(), self, "We already have a connection to", self.remote_peerid
            self.close()
            return

        # Tell all of our peers about each other
        if self.pex:
            for othr in [ x for x in self.other_peers() if x.negotiated ]:
                othr.send('pex', peerid=self.remote_peerid, ip=self.connection.host, port=self.advertised_port)
                self.send('pex', peerid=othr.remote_peerid, ip=othr.connection.host, port=othr.advertised_port)

        self.negotiated = True
        self.check_refs()

    def on_pex_request(self, msg):
        if self.pex:
            for peer in [ x for x in self.other_peers() if x.negotiated and x.remote_peerid != self.remote_peerid ]:
                self.send('pex', peerid=peer.remote_peerid, ip=peer.connection.host, port=peer.advertised_port)

    def on_pex(self, msg):
        if not self.pex:
            return

        peerid = msg['peerid']
        addr = (msg['ip'], msg['port'])
        self.swarm.connect(addr, peerid)

    def do_clone(self):
        assert not self.child
        assert self.cloning
        assert self.swarm.clone

        self.proxy = ProxyListener(self, 0)
        self.proxy.set_env()

        self.cmd = 'clone'
        self.child = Child(self,
                            repr(self.remote_peerid),
                            [ 'git',
                              'clone',
                              '--verbose',
                              '--origin', 'p2p-' + self.remote_peerid,
                              'p2p://' + self.remote_peerid,
                              self.swarm.directory,
                            ])

    def do_fetch(self):
        if not self.negotiated:
            print timestamp(), self, "Cannot fetch, connection not negotiated"
            return

        if self.child:
            print timestamp(), self, "git", repr(self.cmd), "already in progress, cannot fetch"
            return

        if self.swarm.clone:
            print timestamp(), self, "Cloning, cannot fetch"
            return

        self.swarm.git.add_remote(self.remote_peerid)
        self.proxy = ProxyListener(self, 0)
        self.proxy.set_env()

        command = [ 'git',
                    '-C', self.swarm.directory,
                    'fetch',
                    '--verbose',
                    '--prune',
                    '--progress',
                    'p2p-' + self.remote_peerid,
                ]

        if not config.prune:
            command = [ x for x in command if x != '--prune' ]

        self.cmd = 'fetch'
        self.child = Child(self,
                            repr(self.remote_peerid),
                            command)

    def do_merge(self):
        if self.child:
            print timestamp(), self, "git", repr(self.cmd), "already in progress, cannot merge"
            return

        if self.swarm.clone:
            print timestamp(), self, "Cloning, cannot merge"
            return

        branch = self.swarm.git.branch()
        if not branch:
            print timestamp(), self, "Could not determine branch, not merging"
            return

        # There's a tiny race condition right here.  Did the currently selected
        # git branch change between the time we queried it directly above, and
        # the time we use it just below?
        #
        # Life is not without risk.  May git reflog save us all.

        self.proxy = ProxyListener(self, 0)
        self.proxy.set_env()

        self.cmd = 'merge'
        self.child = Child(self,
                            repr(self.swarm.short_sha) + ', merge',
                            [ 'git', '-C', self.swarm.directory,
                              'merge',
                              '--verbose',
                              '--ff-only',
                              'p2p-' + self.remote_peerid + '/' + branch,
                            ])

    def on_ref_change(self, msg):
        self.do_fetch()

    def check_refs(self, update_only=False):
        if not self.negotiated:
            return

        if self.swarm.clone:
            return

        if time.time() - self.last_refs_check <= config.ref_check_interval:
            return

        self.last_refs_check = time.time()
        sig = self.swarm.git.refs_signature()

        if self.refs_sig != sig:
            self.refs_sig = sig

            if not update_only:
                self.swarm.git.update_server_info()
                self.send('ref_change')

    ###
    ### Receiving Files
    ###

    # Called by Proxy()
    def proxy_request_file(self, proxy, filename):
        id_ = self.next_request_id
        self.next_request_id += 1

        self.requests[id_] = proxy
        self.send('file_get', id=id_, file=filename)
        return id_

    # Called by Proxy()
    def proxy_close(self, proxy, cancel=False):
        id_ = proxy.id_

        if not self.requests.has_key(id_):
            print timestamp(), self, "proxy_close() from unknown proxy?", id_, proxy
            return

        if cancel:
            self.send('file_cancel', id=id_)

        del self.requests[id_]

    def on_file_dat(self, msg):
        buf = msg['buf']
        id_ = msg['id']
        proxy = self.requests.get(id_, None)
        chunk = msg['chunk']

        if not proxy:
            print timestamp(), self, "No proxy found for file data ID?"
            return

        try:
            proxy.on_file_dat(buf)
        except Exception as e:
            traceback.print_exc()
            print timestamp(), self, "Proxy failed to handle data, sending cancel"
            self.send('file_cancel', id=id_)
            proxy.close()
        else:
            self.send('file_ack', id=id_, chunk=chunk)

    ###
    ### Sending Files
    ###

    def on_file_get(self, msg):
        filename = msg['file']
        id_ = msg['id']

        get = self.sending.get(id_, None)
        if get:
            raise Exception("Duplicate file_get ID")

        try:
            get = FileGet(self, id_, filename)
        except IOError as e:
            print timestamp(), self, "Open failed:", e
            self.send('file_dat', chunk=0, id=id_, buf='')
            return

        self.sending[id_] = get
        get.on_ack(0)

    def on_file_cancel(self, msg):
        '''
        Sent by remote per to cancel it's request for a file
        '''
        id_ = msg['id']
        get = self.sending.get(id_, None)
        if not get:
            print timestamp(), self, "file_cancel for non-existent sender?"
            print repr(id_), repr(self.sending)
            return

        get.close()
        del self.sending[id_]

    def on_file_ack(self, msg):
        id_ = msg['id']
        get = self.sending.get(id_, None)

        if not get:
            # Very kludgey special case.
            #
            # Because of a quirk of the existing implementation, we'll always
            # send files with the 'chunk' ID starting at 1, not 0.  An
            # exception is in on_file_get() above, where on error we send an
            # EOF using chunk ID 0.  The remote peer will ack that chunk ID,
            # even though we aren't keeping a record of it.  So, if the chunk
            # ID is 0, just don't print anything, just to make the debugging
            # output look nicer.
            if msg['chunk'] != 0:
                print timestamp(), self, "file_ack for non-existent sender?"
            return

        # Returns True if we're done sending
        ret = get.on_ack(msg['chunk'])
        if ret:
            get.close()
            del self.sending[id_]
