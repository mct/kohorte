#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import socket
import time

'''
Miscellaneous utility functions
'''

def my_ip(dest=None):
    '''
    Determine the local IP address by creating a connected UDP socket,
    and then calling getsockname(2) to read back the locally assigned IP.
    '''
    dest = dest or '207.106.1.2'
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)

    try:
        sock.connect((dest, 42))
    except Exception:
        ip, port = '127.0.0.1', 42
    else:
        ip, port = sock.getsockname()

    sock.close()

    return ip

def validate_ip(ip):
    try:
        octets = [ int(x) for x in ip.split('.') ]
        assert len(octets) == 4
        for i in octets:
            assert 0 <= i <= 255
    except:
        return False
    else:
        return True

def timestamp():
    return time.strftime("%H:%M:%S") + ' '
