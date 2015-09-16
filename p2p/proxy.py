#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import time
import socket
import base64
import os
import re

import config

from eventloop import EventLoop
from util import *

class FakeProxy(object):
    '''
    A fake Proxy() class, for debugging, that fetches files and just prints
    them to the debugging output.
    '''
    def __repr__(self):
        return "FakeProxy()"

    def __init__(self, peer, filename):
        self.bytes = 0
        peer.proxy_request_file(self, filename)

    def close(self):
        print timestamp(), self, "Close"

    def on_file_dat(self, buf):
        self.bytes += len(buf)

        if buf == '':
            if self.bytes:
                print timestamp(), self, "EOF, after", self.bytes, "bytes"
            else:
                print timestamp(), self, "File Not Found, Empty file, or Rejected"
            return

        for x in buf.split('\n'):
            print timestamp(), self, repr(x)

class ProxyListener(object):
    '''
    Listener for inbound HTTP proxy requests.  Creates a Proxy() object for
    each connection.
    '''

    def __repr__(self):
        try:
            addr = '%s:%d' % self.addr
            sha = repr(self.peer.swarm.short_sha)
            return 'ProxyListen(%s, %s)' % (sha, addr)
        except:
            return 'ProxyListen(oops, %s)' % id(self)

    def __init__(self, peer, port=0, backlog=4):
        sock = socket.socket()
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('127.0.0.1', port))
        sock.listen(backlog)
        sock.setblocking(False)

        self.peer = peer
        self.sock = sock
        self.addr = sock.getsockname()
        self.closed = False

        # Generate a random username and password for authentication.  We're
        # binding to localhost, but this prevents another user on the system
        # from hijacking our connection.
        self.auth = '%s:%s' % (base64.b32encode(os.urandom(10)), base64.b32encode(os.urandom(10)))
        self.url = 'http://%s@%s:%d/' % (self.auth, self.addr[0], self.addr[1])
        self.children = []

        self.set_env()

        EventLoop.register(self)
        print timestamp(), self, "Proxy URL", self.url

    def set_env(self):
        os.environ['http_proxy'] = self.url

    def reap_children(self):
        self.children = [ x for x in self.children if not x.closed ]

    def on_heartbeat(self):
        self.reap_children()
        if self.peer.closed:
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True

        self.reap_children()
        if self.children:
            print timestamp(), self, "Shutting down, but still children:", self.children

        try:
            self.sock.close()
        except Exception:
            traceback.print_exc()

        EventLoop.unregister(self)

        for x in self.children:
            if not x.closed:
                x.close()

    def fileno(self):
        return self.sock.fileno()

    def wants_readable(self):
        if not self.closed:
            return True

    def wants_writable(self):
        return False

    def on_readable(self):
        ret = self.sock.accept()
        if ret is None:
            return
        sock, addr = ret

        self.children.append(Proxy(self, sock, addr, self.peer))

class Proxy(object):
    '''
    An HTTP proxy, which git-http-remote will connect to.  Parse the request,
    and proxy it by requesting the file from our remote peer.
    '''

    def __repr__(self):
        return "Proxy" + repr(self.addr)

    def __init__(self, listener, sock, addr, peer):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024*128)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024*128)

        self.listener = listener
        self.auth = listener.auth
        self.sock = sock
        self.addr = addr
        self.peer = peer
        self.readbuf = ''
        self.writebuf = ''
        self.bytes_read = 0
        self.request = ''
        self.idle_timeout = time.time()
        self.sent_header = False
        self.eof = False
        self.closed = False
        self.id_ = None
        self.filename = None

        #print timestamp(), self, "Incomming connection from", self.addr
        EventLoop.register(self)

    def idle(self):
        return time.time() - self.idle_timeout

    def on_heartbeat(self):
        if self.request == '' and self.idle() >= config.proxy_idle_timeout:
            print timestamp(), self, "Idle timeout"
            self.close()

    def fileno(self):
        return self.sock.fileno()

    def close(self):
        if self.closed:
            return
        self.closed = True

        #print timestamp(), self, "Closing"

        try:
            self.sock.close()
        except Exception:
            traceback.print_exc()

        if self.eof:
            self.peer.proxy_close(self, cancel=False)
        else:
            self.peer.proxy_close(self, cancel=True)

        EventLoop.unregister(self)

    def wants_writable(self):
        if self.closed:
            return False

        if self.writebuf or self.eof:
            return True

        return False

    def wants_readable(self):
        if not self.closed:
            return True

    def on_writable(self):
        if self.writebuf == '':
            raise Exception("on_writable called but we have no writebuf?")

        try:
            n = self.sock.send(self.writebuf)
            if n > 0:
                self.writebuf = self.writebuf[n:]
        except socket.error, e:
            if e.errno == errno.EINTR:
                print timestamp(), self, "send() EINTR, ignoring"
            if e.errno == errno.EWOULDBLOCK:
                print timestamp(), self, "send() out of SO_SNDBUF space, closing"
                self.close()
            else:
                raise

        if self.eof and self.writebuf == '':
            self.close()

    def on_readable(self):
        buf = self.sock.recv(config.proxy_max_recv)
        if buf == '':
            #print timestamp(), self, "EOF"
            self.close()
            return

        # If we've received more than N bytes without finding the end of the
        # HTTP request, something has gone wrong.  The request should be tiny.
        self.bytes_read += len(buf)
        if self.bytes_read > config.proxy_max_readbuf:
            print timestamp(), self, "More than", config.proxy_max_readbuf, "bytes received, bailing"
            self.close()
            return

        # Don't bother looking at more if we've already successfully parsed a
        # request.  In fact, why is more being sent?
        if self.request:
            return

        buf = buf.replace('\r', '')
        self.readbuf += buf

        # Accumulate bytes until we have the entire HTTP request, and only then
        # call parse().
        head, sep, tail = self.readbuf.partition('\n\n')
        if sep != '\n\n':
            return

        self.request = head
        self.parse()

    def parse(self):
        match = re.match(r'^GET http://p2p/([.a-zA-Z0-9/_-]+)[? ]', self.request)
        if not match:
            raise Exception("Could not validate HTTP verb")

        filename = match.groups()[0]

        match = re.search(r'\nProxy-Authorization: Basic ([^\s]+)\n', self.request)
        if not match:
            raise Exception("No Proxy-Authorization header found")

        encoded = match.groups()[0]
        decoded = base64.b64decode(encoded)

        if decoded != self.auth:
            raise Exception("Authorization failure " + repr(decoded))

        self.filename = filename
        self.id_ = self.peer.proxy_request_file(self, filename)

    def on_file_dat(self, data):
        '''
        Sends a chunked HTTP response, with one chunk corresponding to each
        'file_dat' Message we receive from the remote peer.
        
        If the remote peer sends us a zero length file, assume the file was not
        found, and return 404.  Because we don't decide what the HTTP response
        code will be until we've received the first chunk, the logic that
        determines when we send HTTP headers is a bit messy.
        '''

        headers = [
                'Content-Type: application/octet-stream',
                'Transfer-Encoding: chunked',
                'Connection: close',
                '',
            ]

        if self.eof:
            print timestamp(), self, "oops? Already sent EOF, but received more file data:", repr(data)
            return

        if data == '':
            #print timestamp(), self, "file_get EOF"

            if self.sent_header:
                self.writebuf += '0\r\n\r\n'
            else:
                print timestamp(), self, "File not found (or empty):", self.filename
                for x in ['HTTP/1.1 404 Not Found'] + headers:
                    self.writebuf += x + '\r\n'

            self.eof = True
            return

        if not self.sent_header:
            self.sent_header = True
            for x in ['HTTP/1.1 200 OK'] + headers:
                self.writebuf += x + '\r\n'

        self.writebuf += '%x' % len(data) + '\r\n'
        self.writebuf += data + '\r\n'
