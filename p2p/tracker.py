#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import socket
import struct
import time
import random
import os
import hashlib
import traceback

import config

from peer import Peer
from swarm import Swarm
from eventloop import EventLoop
from util import *

(
 STATE_SOCK,       # 0  # Trying to open a socket
 STATE_SWARM,      # 1  # Picking a warm to announce
 STATE_CONN_ID,    # 2  # Obtaining a connection ID
 STATE_ANNOUNCE,   # 3  # Sending an announce
 STATE_MUTE,       # 4  # Quiet time, give the tracker a break
    ) = range(5)

class Tracker(object):
    '''
    A class to handle connecting to a single BitTorrent UDP tracker.  The
    tracker protocol is documented at http://bittorrent.org/beps/bep_0015.html

    Will periodically try to re-open the socket on socket error (e.g., no route
    to host, while your wifi connection is down).

    For now, all Swarms are announced to each tracker.

    We don't currently tell the tracker when we exit, which isn't very nice.
    '''

    index = []

    @classmethod
    def list(cls):
        return list(cls.index)

    def __repr__(self):
        try:
            return "Tracker(%s, %d)" % (self.host, self.port)
        except:
            return "Tracker(oops, %s)" % id(self)

    def __init__(self, host, port, listen_port):
        if [ x for x in self.index if x.host == host and x.port == port ]:
            raise Exception("Tracker already exists for %s:%d" % (host, port))

        self.closed = False
        self.host = host                # Tracker's hostname
        self.port = port                # Tracker's port
        self.listen_port = listen_port  # Our port, to announce
        self.swarms = {}                # Map of Swarm objects to announcement expire times
        self.transaction_id = random.randint(0, 2**32)
        self.key = random.randint(0, 2**32)
        self.state = STATE_SOCK

        self.sock = None
        self.sock_next_attempt = 0

        self.current_swarm = None

        self.conn_id = None
        self.conn_id_expires = 0
        self.conn_id_next_attempt = 0
        self.conn_id_retry = 0

        self.announce_next_attempt = 0
        self.announce_retry = 0

        self.unmute_time = 0

        self.index.append(self)
        EventLoop.register(self)
        print timestamp(), self, "Registered"

    def add_swarm(self, swarm):
        if self.swarms.has_key(swarm):
            return
        print timestamp(), self, "Adding", swarm
        self.swarms[swarm] = 0

    def remove_swarm(self, swarm):
        if self.swarms.has_key(swarm):
            del self.swarms[swarm]

    def wants_readable(self):
        if self.sock:
            return True

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        if self.closed:
            return
        self.closed = True

        # If we were nice, we'd tell the tracker we're going away
        print timestamp(), self, "Closing"
        if self.sock:
            self.sock.close()
            self.sock = None

    def send(self, buf):
        assert self.state in [ STATE_CONN_ID, STATE_ANNOUNCE ]
        assert self.sock

        print timestamp(), self, "-->", len(buf), "bytes"

        try:
            self.sock.send(buf)
        except socket.error as e:
            print timestamp(), self, "Socket error, will try again later:", str(e)
            self.goto_state_sock()

    def on_readable(self):
        try:
            buf = self.sock.recv(1024*10)
        except socket.error as e:
            print timestamp(), self, "Socket error, will try again later:", str(e)
            self.goto_state_sock()
            return

        print timestamp(), self, "<--", len(buf), "bytes"

        try:
            self.parse(buf)

        except Exception:
            print timestamp(), self, "parsing/responding to packet failed"
            traceback.print_exc()
            print

    def parse(self, buf):
        if len(buf) < 16:
            print timestamp(), self, "Runt:", hex_repr(buf)
            return

        (action, transaction_id) = struct.unpack('!LL', buf[:8])

        if transaction_id != self.transaction_id:
            print timestamp(), self, "Transaction ID mismatch:", hex_repr(buf)
            return

        # Connection ID response
        if action == 0:
            assert len(buf) == 16
            (conn_id,) = struct.unpack('!Q', buf[8:])
            self.conn_id = conn_id
            print timestamp(), self, "Connection ID:", hex(self.conn_id)
            self.conn_id_expires = time.time() + 60
            self.goto_state_announce()
            return

        # Announce response
        elif action == 1:
            if len(buf) < 20:
                print timestamp(), self, "Announce runt:", hex_repr(buf)

            (interval, leechers, seeders) = struct.unpack('!LLL', buf[8:20])
            buf = buf[20:]
            peers = []

            while len(buf) >= 6:
                ip = socket.inet_ntoa(buf[:4])
                (port,) = struct.unpack('!H', buf[4:6])
                buf = buf[6:]
                print timestamp(), self, "Found peer for", self.current_swarm, "at", ip, port
                peers.append((ip, port))

            if not peers:
                print timestamp(), self, "No peers found for", self.current_swarm

            if buf:
                print timestamp(), self, "Leftover bytes in announce response?", repr(buf)

            interval = min(interval, config.tracker_max_interval)
            print timestamp(), self, "Will ask for more peers in", interval, "seconds"
            self.swarms[self.current_swarm] = time.time() + interval

            for x in peers:
                self.current_swarm.connect(x)

            self.goto_state_swarm()
            return

        # Error message.  Old versions of OpenTracker had a bug where it didn't
        # call htonl() for the error code, so check both big and little endian.
        elif action == 3 or action == 0x03000000:
            print timestamp(), self, "Tracker reports error:", repr(buf[8:])
            self.goto_state_mute()
            return

        else:
            print timestamp(), self, "Unknown action type %s: %s" % (action, self.hex_repr(buf))
            return

    def hex_repr(self, buf):
        '''
        Returns a hexdump grouped by 32bit words
        '''
        hexbuf = buf.encode('hex')
        grouping = 8 # eight characters of hexadecimal == 4 bytes
        line = [ hexbuf[i:i+grouping] for i in range(0, len(hexbuf), grouping) ]
        return repr(len(buf)) + ' bytes:  ' + ' '.join(line)

    ###

    def goto_state_sock(self):
        self.state = STATE_SOCK
        print timestamp(), self, "State", self.state
        if self.sock:
            self.sock.close()
        self.sock = None
        self.sock_next_attempt = time.time() + config.tracker_socket_retry
        self.handle_state_sock()

    def handle_state_sock(self):
        assert not self.sock

        if time.time() < self.sock_next_attempt:
            return

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*128)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*128)
            self.sock.setblocking(False)
            self.sock.connect((self.host, self.port))
        except socket.error as e:
            print timestamp(), self, "Socket error, will try again later:", str(e)
            self.goto_state_sock()
        else:
            print timestamp(), self, "Socket", self.sock
            self.goto_state_swarm()

    ###

    def goto_state_swarm(self):
        self.state = STATE_SWARM
        print timestamp(), self, "State", self.state
        self.current_swarm = None
        self.handle_state_swarm()

    def handle_state_swarm(self):
        assert not self.current_swarm

        candidates = [ swarm for swarm, expire in self.swarms.items() if expire <= time.time() ]
        random.shuffle(candidates)
        if not candidates:
            return

        self.current_swarm = candidates[0]
        print timestamp(), self, "Starting announcement for", self.current_swarm
        self.goto_state_conn_id()

    ###

    def goto_state_conn_id(self):
        self.state = STATE_CONN_ID
        print timestamp(), self, "State", self.state
        self.conn_id_next_attempt = 0
        self.conn_id_retry = 0
        self.handle_state_conn_id()

    def handle_state_conn_id(self):
        if self.conn_id and time.time() < self.conn_id_expires:
            self.goto_state_announce()
            return

        if time.time() < self.conn_id_next_attempt:
            return

        retry_text = ''
        if self.conn_id_retry > 0:
            retry_text = ', retry %d' % self.conn_id_retry

        print timestamp(), self, "Sending Connection ID request" + retry_text
        self.send(struct.pack("!QLL", 0x41727101980, 0, self.transaction_id))
        self.conn_id_next_attempt = time.time() + config.tracker_retry_time * 2**min(self.conn_id_retry, 4)
        self.conn_id_retry += 1

    ###

    def goto_state_announce(self):
        self.state = STATE_ANNOUNCE
        print timestamp(), self, "State", self.state
        self.announce_next_attempt = 0
        self.announce_retry = 0
        self.handle_state_announce()

    def handle_state_announce(self):
        assert self.conn_id
        if self.conn_id_expires < time.time():
            print timestamp(), self, "Connection ID expired"
            self.goto_state_conn_id()
            return

        if not self.current_swarm in [ x for x in Swarm.list() if not x.closed ]:
            self.goto_state_swarm()
            return

        if time.time() < self.announce_next_attempt:
            return

        retry_text = ''
        if self.announce_retry > 0:
            retry_text = ', retry %d' % self.announce_retry

        print timestamp(), self, "Sending announce for %s%s" % (self.current_swarm, retry_text)

        buf = ''
        buf += struct.pack('!QLL', self.conn_id, 1, self.transaction_id)
        buf += self.current_swarm.sha.decode('hex')
        buf += hashlib.sha1(Peer.my_peerid).digest()
        buf += struct.pack('!QQQLLLlH', 0, 0, 0, 1, 0, self.key, -1, self.listen_port)
        self.send(buf)

        self.announce_next_attempt = time.time() + config.tracker_retry_time * 2**min(self.announce_retry, 4)
        self.announce_retry += 1

    ###

    def goto_state_mute(self):
        self.state = STATE_MUTE
        print timestamp(), self, "State", self.state
        self.unmute_time = time.time() + config.tracker_mute_time

    def handle_state_mute(self):
        if time.time() < self.unmute_time:
            return
        print timestamp(), self, "Unmuted"
        self.conn_id = None
        self.goto_state_swarm()

    ###

    def on_heartbeat(self):
        # Kludge for now.  Just have every tracker announce every swarm
        for x in Swarm.list():
            self.add_swarm(x)

        # Cleanup old swarms
        for x in [x for
                  x in self.swarms.keys()
                  if x.closed or x not in Swarm.list()]:
            print timestamp(), self, "Swarm seems to have gone away, removing:", x
            del self.swarms[x]

        if   self.state == STATE_SOCK:     self.handle_state_sock()
        elif self.state == STATE_SWARM:    self.handle_state_swarm()
        elif self.state == STATE_CONN_ID:  self.handle_state_conn_id()
        elif self.state == STATE_ANNOUNCE: self.handle_state_announce()
        elif self.state == STATE_MUTE:     self.handle_state_mute()
        else:                              raise Exception("State machine broken?")
