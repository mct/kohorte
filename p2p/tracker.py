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
        others = [ x for
                   x in self.index
                   if x.host == host and x.port == port ]
        if others:
            raise Exception("Tracker already exists for %s:%d" % (host, port))

        self.host = host
        self.port = port
        self.listen_port = listen_port

        self.sock = None
        self.socket_last_try = 0

        self.closed = False
        self.swarms = {} # maps Swarm objects to announcement expire times

        self.transaction_id = random.randint(0, 2**32)
        self.key = random.randint(0, 2**32)

        # If a tracker sends us an error, stay quiet for a while.  Either
        # set to None, or the timestamp when we can start speaking again.
        self.mute = None

        # Information related to obtaining our Connection ID
        self.conn_id_packet = ''
        self.conn_id_time = 0
        self.conn_id_retries = 0
        self.conn_id_last_sent = 0
        self.conn_id = None

        # Information related to the current announce we're trying to send
        self.announce_packet = ''
        self.announce_retries = 0
        self.announce_last_sent = 0
        self.announce_swarm = None # the Swarm we've currently selected to announce

        self.index.append(self)
        self.open_socket()

        EventLoop.register(self)
        print timestamp(), self, "Registered"

    def open_socket(self):
        if self.sock:
            return

        if time.time() < (self.socket_last_try + config.tracker_socket_retry):
            return

        if self.socket_last_try != 0:
            print timestamp(), self, "Opening socket"

        self.socket_last_try = time.time()

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*128)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*128)
            self.sock.setblocking(False)
            self.sock.connect((self.host, self.port))
        except socket.error as e:
            print timestamp(), self, "Socket error, will try again later:", str(e)
            self.socket_last_try = time.time()
            self.sock = None
            return

    def wants_readable(self):
        if self.sock:
            return True

    def fileno(self):
        return self.sock.fileno()

    def add_swarm(self, swarm):
        if self.swarms.has_key(swarm):
            return
        print timestamp(), self, "Adding", swarm
        self.swarms[swarm] = 0

    def remove_swarm(self, swarm):
        if self.swarms.has_key(swarm):
            del self.swarms[swarm]

    def close(self):
        if self.closed:
            return
        self.closed = True

        # If we were nice, we'd tell the tracker we're going away
        print timestamp(), self, "Closing"
        if self.sock:
            self.sock.close()
            self.sock = None

    def buf_repr(self, buf):
        '''
        Returns a hexdump, grouped by 32bit words.
        '''
        hexbuf = buf.encode('hex')
        grouping = 8 # eight characters of hexadecimal == 4 bytes
        line =  [ hexbuf[i:i+grouping] for i in range(0, len(hexbuf), grouping) ]
        return repr(len(buf)) + ' bytes:  ' + ' '.join(line)

    def parse(self, buf):
        if len(buf) < 16:
            print timestamp(), self, "Runt"
            return

        (action, transaction_id) = struct.unpack('!LL', buf[:4*2])

        if transaction_id != self.transaction_id:
            print timestamp(), self, "Transaction ID mismatch"
            return

        ##

        # Connection ID response
        if action == 0:
            assert len(buf) == 16
            (conn_id,) = struct.unpack('!Q', buf[8:])
            self.conn_id = conn_id
            print timestamp(), self, "Connection ID:", hex(self.conn_id)
            self.on_heartbeat()
            return

        # Announce response
        elif action == 1:
            if len(buf) < 20:
                print timestamp(), self, "Announce runt"

            (interval, leechers, seeders) = struct.unpack('!LLL', buf[8:20])
            buf = buf[20:]

            peers = []

            while len(buf) >= 6:
                ip = socket.inet_ntoa(buf[:4])
                (port,) = struct.unpack('!H', buf[4:6])
                buf = buf[6:]
                print timestamp(), self, "Found peer for", self.announce_swarm, "at", ip, port
                peers.append((ip, port))

            if buf:
                print timestamp(), self, "Leftover announce response bytes?", repr(buf)

            interval = max(interval, config.max_tracker_interval)
            print timestamp(), self, "Will ask for more peers for", self.announce_swarm, "in", interval, "seconds"
            self.swarms[self.announce_swarm] = time.time() + interval

            for x in peers:
                self.announce_swarm.connect(x)

            self.announce_swarm = None
            return

        # Error message
        elif action == 3:
            print timestamp(), self, "Tracker reports error:", repr(buf[8:])
            self.conn_id = False
            self.mute = time.time() + config.tracker_mute_time
            return

        else:
            print timestamp(), self, "Unknown action type %s. Entire packet: %s" % (action, self.buf_repr(buf))
            return

    def on_readable(self):
        try:
            buf = self.sock.recv(10240)
        except socket.error as e:
            print timestamp(), self, "Socket error, will try again in", config.max_tracker_interval, "seconds:", e
            self.sock.close()
            self.sock = None
            self.socket_last_try = time.time()
            return

        print timestamp(), self, "<--", len(buf), "bytes"

        try:
            self.parse(buf)
        except Exception:
            print timestamp(), self, "Decoding packet failed"
            traceback.print_exc()
            print

    def send(self, buf):
        if not self.sock:
            print timestamp(), self, "Refusing to send while socket is down"
            return

        print timestamp(), self, "-->", len(buf), "bytes"

        try:
            self.sock.send(buf)
        except socket.error as e:
            print timestamp(), self, "Socket error, will try again later:", str(e)
            self.sock.close()
            self.sock = None
            self.socket_last_try = time.time()

    def on_heartbeat(self):
        self.open_socket()

        if self.mute and self.mute < time.time():
            return

        # Kludge for now.  Just have every tracker announce every swarm
        for x in Swarm.list():
            self.add_swarm(x)

        # Remove swarms no longer in use
        for x in self.swarms.keys():
            if x.closed or x not in Swarm.list():
                print timestamp(), self, "Swarm seems to have gone away, removing:", x
                del self.swarms[x]

        ##

        if not self.sock:
            return

        # If the swarm we were trying to announce no longer exists, forget it
        if not self.swarms.has_key(self.announce_swarm):
            self.announce_swarm = None

        # Pick a random swarm to announce
        if not self.announce_swarm:
            swarms = [swarm for swarm,expire in self.swarms.items() if expire <= time.time() ]
            random.shuffle(swarms)
            if not swarms:
                return
            self.announce_swarm = swarms.pop()
            print timestamp(), self, "Starting announcement for", self.announce_swarm
        
        # Check if Connection ID is expired
        if self.conn_id and time.time() < self.conn_id_time + 60:
            print timestamp(), self, "Connection ID expired"
            self.conn_id = None

        # First obtain a connection ID
        if not self.conn_id:
            # Very, very lame. This needs to be re-factored so this is no
            # longer a problem.  Without it, we delay 30 seconds after the
            # first packet, rather than 15.
            offset = self.conn_id_retries
            if self.conn_id_retries > 0:
                offset -= 1

            if self.conn_id_last_sent and time.time() < self.conn_id_last_sent + 15 * 2**offset:
                return

            print timestamp(), self, "Sending Connection ID request, retry", self.conn_id_retries
            self.conn_id_retries += 1
            self.conn_id_retries = min(self.conn_id_retries, 4)
            self.conn_id_last_sent = time.time()
            buf = struct.pack("!QLL", 0x41727101980, 0, self.transaction_id) # action:0, ConnID request
            self.send(buf)
            return

        if self.announce_last_sent and time.time() < self.announce_last_sent + 5 * 2**self.announce_retries:
            return

        print timestamp(), self, "Sending announce request for", self.announce_swarm, "retry", self.announce_retries
        self.announce_retries += 1
        self.announce_retries = min(self.announce_retries, 4)
        self.announce_last_sent = time.time()

        buf = ''
        buf += struct.pack('!QLL', self.conn_id, 1, self.transaction_id) # action:1, announce
        buf += self.announce_swarm.sha.decode('hex')
        buf += hashlib.sha1(Peer.my_peerid).digest()
        buf += struct.pack('!QQQLLLlH', 0, 0, 0, 1, 0, self.key, -1, self.listen_port)
        self.send(buf)
