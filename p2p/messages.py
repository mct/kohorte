#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

'''

The definitive list of all Kohorte Protocol Messages.  Effectively, this
defines the protocol.  The list includes restrictions on each Message
argument, enforcement of which is implemented in connection.py.

'''

import util

MessageTypes = {
        'helo': {
            'protocol': { 'type':str, 'fixed_value':'p2p-git' }, 
            'major':    { 'type':int, 'fixed_value':0 },
            'minor':    { 'type':int, 'fixed_value':1 },
            'peerid':   { 'type':str, 'min_len':3, 'max_len':30, 'regex':r'^[A-Za-z0-9_-]+$' },
            'swarmid':  { 'type':str, 'len':40, 'regex':r'^[0-9a-f]+$' },
            'port':     { 'type':int, 'min':1, 'max':0xfffe },
            'client':   { 'type':str, 'min':1, 'max':30, },
        },

        'ping': {},
        'pong': {},

        # Peer exchange
        'pex_request': {},
        'pex': {
            'peerid': { 'type':str, 'min_len':3, 'max_len':30, 'regex':r'^[A-Za-z0-9_-]+$' },
            'ip':     { 'type':str, 'function':util.validate_ip },
            'port':   { 'type':int, 'min':1, 'max':0xffff },
        },

        # Sent to notify the remote peer that our local refs have changed, and
        # they should perform a git fetch against us.
        'ref_change': {},

        # Request a file from the remote peer.  It will start sending
        # 'file_dat' messages with the file contents.  'id' is an identifier
        # for the data stream set by the requester.  It is inconsequential to
        # the remote peer if the id is not unique.
        'file_get': {
            'file': { 'type':str, 'min_len':1, 'regex':r'^[.a-zA-Z0-9/_-]+$' },
            'id':   { 'type':int, 'min':0 },
        },

        # File data.  'id' is the identifier set in 'file_get'
        #
        # 'chunk' is the chunk (block) number, always incrementing by one.  Due
        # to implementation specific logic, the first block we send is number
        # 1, not number 0.  A remote peer shouldn't care, it simply acks the
        # last (highest) chuck ID it's seen.
        #
        # EOF is indicated by a zero-length 'buf'
        'file_dat': {
            'id':    { 'type':int, 'min':0 },
            'chunk': { 'type':int, 'min':0 },
            'buf':   { 'type':str }
        },

        # Only sent in one direction, from the requesting peer to the sending
        # peer, indicating the HTTP Proxy that requested the file disappeared.
        #
        # To perform the equivalent of a "cancel" message in the other
        # direction, the sending party can send EOF.  There's no way in HTTP
        # to tell git the file transfer was cancelled, anyway, so no need to
        # create a special Message for it.
        'file_cancel': {
            'id':   { 'type':int, 'min':0 },
        },

        'file_ack': {
            'id':    { 'type':int, 'min':0 },
            'chunk': { 'type':int, 'min':0 },
        },

        # Not used at the moment; a remote peer just closes the TCP connection.
        # In the future, may be useful to inform the remote peer why it's going
        # away.
        #
        #'error': { 'error': { 'type':str, }, },
        #'quit': {},
    }
