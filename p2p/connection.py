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

(STATE_START, STATE_LEN, STATE_WHITE, STATE_DATA) = range(4)

def repr_msg(msg):
    '''
    Pretty-print a Kohorte message
    '''

    msg = msg.copy()

    # These just clutter things up, don't display them
    prune = [
        'msg',
        'protocol',
        'major',
        'minor',
        'client',
        'buf',
    ]

    # Order in which to display
    order = [
        'swarmid',
        'peeird',
        'ip',
        'port',
        'client',
        'id ',
        'file',
        'chunk',
        'buf',
    ]

    out = []

    for x in prune:
        if msg.has_key(x):
            del msg[x]

    for x in order:
        if not msg.has_key(x):
            continue
        out.append('%s: %s' % (repr(x), repr(msg[x])))
        del msg[x]

    for x in sorted(msg.keys()):
        out.append('%s: %s' % (repr(x), repr(msg[x])))

    return '{' + ', '.join(out) + '}'

class PeerConnection(object):
    '''
    Only instantiated by Peer().

    Manages the TCP connection for a single peer, and parsing the TCP
    bytestream into individual protocol Messages.  Messages are bencoded
    dictionaries which appear on the the wire prefixed with a length field,
    of four ASCII hexadecimal characters.  For human readability, optional
    whitespace is permitted before and after both the length field and the
    bencoded Message.  Valid Messages are defined by the MessageTypes
    structure in messages.py.
    '''

    def __repr__(self):
        try:
            if self.addr:
                addr = "%s:%d" % self.addr
            else:
                addr = ''
            if self.peer.swarm:
                sha = self.peer.swarm.short_sha
            else:
                sha = 'Inbound'
            peerid = self.peer.__repr_peerid__()
            return "Conn(%s, %s, %s)" % (repr(peerid), repr(sha), addr)
        except:
            return "Conn(oops, %s)" % id(self)

    def __init__(self, peer, sock, addr):
        if sock is None:
            # No sock argument means outbound connection
            sock = socket.socket()
            sock.setblocking(False)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*128)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*128)
            self.connected = False
        else:
            self.connected = True

        self.peer = peer
        self.sock = sock
        self.addr = addr

        self.host = addr[0]
        self.port = addr[1]

        self.writebuf = ''
        self.readbuf = ''
        self.parse_state = STATE_START
        self.parse_len = 0
        self.helo = False
        self.last_read_time = time.time()
        self.last_ping_time = time.time()
        self.closed = False

        if not self.connected:
            print timestamp(), self, "Connecting to", self.addr
            try:
                self.sock.connect(self.addr)
            except socket.error as e:
                if e.errno != errno.EINPROGRESS:
                    raise
            else:
                print timestamp(), self, "Connected immediately?"
                self.on_connect()

        EventLoop.register(self)

    def idle(self):
        return time.time() - self.last_read_time

    def last_ping(self):
        return time.time() - self.last_ping_time

    def on_heartbeat(self):
        if self.peer.closed:
            print timestamp(), self, "Peer is closed?"
            self.close()
            return

        if not self.connected and self.idle() >= config.connect_timeout:
            print timestamp(), self, "Connect timeout"
            self.close()
            return

        if not self.helo and self.idle() >= config.helo_timeout:
            print timestamp(), self, "HELO timeout"
            self.close()
            return

        if self.idle() >= config.idle_timeout:
            print timestamp(), self, "Idle timeout"
            self.close()
            return

        if self.idle() >= config.idle_ping + random.randint(0, config.idle_ping/2) and self.last_ping() >= config.idle_ping:
            self.send_msg('ping')
            self.last_ping_time = time.time()
            # no return

        self.peer.on_heartbeat()

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        if self.closed:
            return
        self.closed = True

        print timestamp(), self, "Closing Connection"

        if self.writebuf:
            print timestamp(), self, "Flushing %d byte writebuf" % len(self.writebuf)
            try:
                self.on_writable()
            except Exception:
                print timestamp(), self, "Failed to flush: %s" % repr(self.writebuf)

        try:
            self.sock.close()
        except Exception:
            traceback.print_exc()

        try:
            self.peer.close()
        except Exception:
            traceback.print_exc()

        EventLoop.unregister(self)

    def wants_writable(self):
        if self.closed:
            return False

        if not self.connected:
            return True

        if self.writebuf:
            return True

        return False

    def wants_readable(self):
        if self.closed:
            return False

        if not self.connected:
            return False

        return True

    def on_connect(self):
        ret = self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if ret != 0:
            print timestamp(), self, "Connection error:", os.strerror(ret)
            self.close()
            return

        print timestamp(), self, "Connected"
        self.last_read_time = time.time()
        self.last_ping_time = time.time()
        self.connected = True
        self.peer.on_connect()

    def on_writable(self):
        if not self.connected:
            self.on_connect()
            return

        if self.writebuf == '':
            raise Exception("on_writable called but no writebuf?")

        try:
            n = self.sock.send(self.writebuf)
            if n > 0:
                self.writebuf = self.writebuf[n:]
        except socket.error, e:
            if e.errno == errno.EINTR:
                print timestamp(), self, "send() EINTR, ignoring"
            if e.errno == errno.EWOULDBLOCK:
                print timestamp(), self, "send() out of SO_SNDBUF space"
                raise
            else:
                raise

    def on_readable(self):
        try:
            buf = self.sock.recv(config.msg_max_recv)
        except socket.error as e:
            if e.errno == errno.ECONNRESET:
                print timestamp(), self, "Connection reset by peer"
            else:
                print timestamp(), self, "Unhandled socket error:", e
            return

        if buf == '':
            print timestamp(), self, "EOF"
            self.close()
            return

        self.last_read_time = time.time()
        self.readbuf += buf

        try:
            self.parse()

        except ProtocolError as e:
            print timestamp(), self, "Protocol error, shutting down:", str(e)
            self.close()

    def parse(self):
        '''
        A state engine to examine the current readbuf.  Raises ProtocolError()
        exception if an error is encountered.  This is totally a shotgun
        parser, and I don't have any test cases yet.  Sorry, Meredith. :-(
        '''

        while True:
            # It's possible that while dispatching a parsed command earlier in
            # this function call that the Peer closed the connection, so double
            # check that the connection is still open before continuing.
            if self.closed:
                print timestamp(), self, len(self.readbuf), "bytes in readbuf at close:", repr(self.readbuf)
                return

            if len(self.readbuf) == 0:
                break

            if self.parse_state in (STATE_START, STATE_WHITE):
                self.readbuf = self.readbuf.lstrip()
                if len(self.readbuf) > 0:
                    self.parse_state += 1

            if self.parse_state == STATE_LEN:
                if len(self.readbuf) < config.msg_len_bytes:
                    break

                try:
                    self.parse_len = int(self.readbuf[:config.msg_len_bytes], 16)
                    self.readbuf = self.readbuf[config.msg_len_bytes:]
                except ValueError:
                    raise ProtocolError("Could not decode length prefix: " + repr(self.readbuf))

                if self.parse_len > config.msg_max_len:
                    raise ProtocolError("Message too long")

                self.parse_state += 1

            if self.parse_state == STATE_DATA:
                if len(self.readbuf) < self.parse_len:
                    break

                buf = self.readbuf[: self.parse_len]
                self.readbuf = self.readbuf[self.parse_len :]
                self.parse_state = 0

                try:
                    msg = bdecode(buf)
                except Exception:
                    raise ProtocolError("bdecode failed")

                try:
                    name, msg = self.validate_message(msg)
                except DecoderError as e:
                    #print timestamp(), self, "Failed:", repr(msg)
                    #raise ProtocolError("Message validation failed: " + str(e))
                    print timestamp(), self, "<-- Failed:", repr(msg)
                    raise ProtocolError(str(e))

                print timestamp(), self, "<-- ", name, repr_msg(msg)

                if name == 'helo' and self.helo:
                    raise ProtocolError("Double handshake")

                if name == 'helo':
                    self.helo = True

                if not self.helo:
                    raise ProtocolError("First message must be HELO")

                self.dispatch(name, msg)

    def validate_message(self, msg):
        '''
        Called by the parser to validate a Message, after bdecoding the blob
        into a python dict.  Raises a DecoderError() error if validation fails.
        Otherwise, returns a tuple of the Message type, and a dict containing
        its arguments.
        '''

        if not type(msg) == dict:
            raise DecoderError('Message must be a dictionary')

        try:
            name = msg['msg']
        except KeyError:
            raise DecoderError("Required field 'msg' is missing")
        else:
            del msg['msg']

        try:
            args = MessageTypes[name]
        except KeyError:
            raise DecoderError("Unknown message type")

        for field in args.keys():
            if not msg.has_key(field):
                raise DecoderError("Required field '%s' is missing" % field)

        # Enforce the Message argument restrictions, defined in messages.py.
        # There is *way* too much repetition of string literals in here.  This
        # entire section needs to be re-worked.
        for field, restriction in args.iteritems():

            if restriction.has_key('type'):
                if not type(msg[field]) == restriction['type']:
                    raise DecoderError("Field '%s' failed check: type %s" % (field, restriction['type']))

            if restriction.has_key('fixed_value'):
                if not msg[field] == restriction['fixed_value']:
                    raise DecoderError("Field '%s' failed check: fixed_value %s" % (field, restriction['fixed_value']))

            if restriction.has_key('min_len'):
                if not len(msg[field]) >= restriction['min_len']:
                    raise DecoderError("Field '%s' failed check: min_len %s" % (field, restriction['min_len']))

            if restriction.has_key('max_len'):
                if not len(msg[field]) <= restriction['max_len']:
                    raise DecoderError("Field '%s' failed check: max_len %s" % (field, restriction['max_len']))

            if restriction.has_key('len'):
                if not len(msg[field]) == restriction['len']:
                    raise DecoderError("Field '%s' failed check: len %s" % (field, restriction['len']))

            if restriction.has_key('regex'):
                if not re.match(restriction['regex'], msg[field]):
                    raise DecoderError("Field '%s' failed check: regex %s" % (field, restriction['regex']))

            if restriction.has_key('function'):
                if not restriction['function'](msg[field]):
                    raise DecoderError("Field '%s' failed check: function %s" % (field, repr(restriction['function'])))



        # For now, don't reject messages for having extra fields, but remote
        # them before processing the Message.  We don't want to access them
        # accidentally and getting unvalidated input.
        for field in msg.keys():
            if not args.has_key(field):
                del msg[field]

        return name, msg

    def dispatch(self, name, msg):
        '''
        Find an appropriate Message handler in the Peer() object, consisting of
        the Message named prefixed with "on_".  Do not generate an Exception if
        no handler is found, just log it and go on.
        '''
        f = getattr(self.peer, 'on_' + name, None)

        if f:
            f(msg)
        else:
            print timestamp(), self, "No handler found for", name, msg

    def send_msg(self, name_, **msg):
        '''
        Send an outgoing Message, translating function keyword arguments into
        the Message arguments.  Before sending the message down the wire, run
        our own validation checks against it, and raise a DecoderError()
        Exception if it fails.

        After validation, bencode the Python dict, and prefix it with an ASCII
        length field.  For human readability, put a space between length field
        and the bencoded blob, and send a \r\n after the blob.
        '''
        try:
            msg['msg'] = name_
            self.validate_message(msg.copy())
        except DecoderError as e:

            # This debugging output is hard to read, but fortunately it
            # shouldn't happen very often.

            print timestamp(), self, "Our own outgoing message failed validation?", msg
            self.close()
            raise

        buf = bencode(msg)
        buf = ('%0' + str(config.msg_len_bytes) + 'x') % len(buf) + ' ' + buf + '\r\n'

        del msg['msg']
        print timestamp(), self, "--> ", name_, repr_msg(msg)
        self.writebuf += buf
