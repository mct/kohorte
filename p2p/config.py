#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

'''
Constants, and configurable items
'''

import socket
import getpass

# attributes that should be treated read only
danger = [
        'danger',
        'socket',
        'getpass',
        'msg_len_bytes',
        'msg_max_len',
        'version',
    ]

version = 0.1
major_version = 0
minor_version = 1

## Peer, Connection
listen_port = 0
default_peerid = getpass.getuser() + '-' + socket.gethostname()
ref_check_interval = 10
automerge = False
connect_timeout = 30
helo_timeout = 10
idle_ping = 200
idle_timeout = idle_ping * 3.5
pex = True
prune = True
dampen_time = 25
max_peers = 100

## Trackers
default_tracker = 'tr.iz.is:6969'
max_tracker_interval = 60*30
tracker_socket_retry = 10
tracker_mute_time = 20

## Local Peer Discovery
mcast_grp = '239.192.152.143'   # The bittorrent multicast group
mcast_port = 6771 + 1           # The bittorrent port, plus 1

## Proxy
proxy_idle_timeout = 60
proxy_max_recv = 1024*8
proxy_max_readbuf = 1024*32 # die if this many bytes read while parsing HTTP

## Message parsing
msg_len_bytes = 4
msg_max_len = 1024*16
msg_max_recv = (msg_max_len + msg_len_bytes) * 8
file_get_chunk_size = int(msg_max_len * 0.66)
file_get_window = 5 # How many file chunks should be sent a once?
