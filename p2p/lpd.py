#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import socket
import struct
import time
import base64
import os

import swarm
import peer
import config

from eventloop import EventLoop
from util import *

class LPD(object):
    '''
    Sends and receives Local Peer Discovery multicast messages.  Will attempt
    to re-open the socket periodically on socket errors, which happen with some
    frequency if your wireless connection goes up and down, or if you suspend
    and resume your laptop, etc.
    '''

    index = []

    def __repr__(self):
        return "LPD()"

    def __init__(self, port, announce_time=600, sock_attempt_time=5):
        if self.index:
            raise Exception("An instance already exists?")

        self.port = port
        self.announce_time = announce_time
        self.sock_attempt_time = sock_attempt_time
        self.last_sock_attempt = 0
        self.sock = None
        self.open_socket()
        self.index.append(self)
        EventLoop.register(self)

    def close(self):
        raise Exception("Something terrbile has happened, listener was asked to close")

    def wants_readable(self):
        if self.sock:
            return True

    def wants_writable(self):
        return False

    def fileno(self):
        return self.sock.fileno()

    def open_socket(self):
        if self.sock:
            print timestamp(), self, "Double call to open_socket()?  self.sock ==", repr(self.sock)
            return

        if time.time() - self.last_sock_attempt < self.sock_attempt_time:
            return
        self.last_sock_attempt = time.time()

        mreq = struct.pack("4sl", socket.inet_aton(config.mcast_grp), socket.INADDR_ANY)

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR,      1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,  1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.INADDR_ANY)
            sock.bind((config.mcast_grp, config.mcast_port))
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except socket.error as e:
            print timestamp(), self, "Error opening socket, will try again later:", e
        else:
            self.sock = sock
            self.last_announce = 0
            print timestamp(), self, "Listening"

    def show(self, inbound, buf, comment=''):
        if inbound:
            direction = '<--'
        else:
            direction = '-->'

        print timestamp(), self, direction, repr(buf), comment

    def on_heartbeat(self):
        if not self.sock:
            self.open_socket()
        if not self.sock:
            return

        if time.time() - self.last_announce < self.announce_time:
            return

        self.last_announce = time.time()

        for s in swarm.Swarm.list():
            buf = '%s %s %d %s' % (s.sha, my_ip(), self.port, peer.Peer.my_peerid)

            try:
                self.sock.sendto(buf, 0, (config.mcast_grp, config.mcast_port))
            except socket.error as e:
                print timestamp(), self, "sendto error, will try opening socket again later:", e
                self.sock.close()
                self.sock = None
                self.last_sock_attempt = time.time()
                return
            else:
                self.show(False, buf)

    def on_readable(self):
        try:
            buf = self.sock.recv(1024)
        except socket.error as e:
            print timestamp(), self, "recv error, will try opening socket again later:", e
            self.sock.close()
            self.sock = None
            self.last_sock_attempt = time.time()
            return

        try:
            sha, host, port, remote_peerid = buf.split()
            port = int(port)
            addr = ((host, port))
        except Exception as e:
            self.show(True, buf, '# Not LPD message, ignoring: ' + str(e))
            return

        if remote_peerid == peer.Peer.my_peerid:
            self.show(True, buf, '# Our own, ignoring')
            return

        s = swarm.Swarm.get(sha)
        if not s:
            self.show(True, buf, '# Unknown swarm')
            return

        if [ x for x in peer.Peer.list() if x.swarm == s and x.remote_peerid == remote_peerid ]:
            self.show(True, buf, '# Already connected')
            return

        self.show(True, buf)
        print timestamp(), self, "Found peer for", sha, "at", addr
        s.connect(addr, remote_peerid)

    @classmethod
    def update(cls):
        '''
        Force an update, e.g. when a Swarm is added
        '''

        if not cls.index:
            return

        x = cls.index[0]
        x.last_announce = 0
        x.last_sock_attempt = 0
        x.on_heartbeat()
