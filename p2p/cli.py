#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import traceback
import sys
import os
import operator

import config

from swarm import Swarm
from peer import Peer
from proxy import ProxyListener, FakeProxy
from lpd import LPD
from tracker import Tracker
from eventloop import EventLoop
from util import *

class NoPeer(Exception):
    pass
class NoSwarm(Exception):
    pass

class CLI(object):
    '''
    The CLI handling system has some really nice features, and some sharp
    corners.

    Methods named 'cmd_XXX' become valid CLI commands.  Commands that return
    False (and only False) are interpreted as errors.  Any other value
    (including None) is treated as success.  (Functions in Python without an
    explicit return statement return None, meaning commands may use an implicit
    return.)

    The "list" command displays a list of Swarms, and Peers, in an order
    designed to make sense to the user.

    The CLI has a notion of the "current peer", and "current swarm".  Commands
    that operate on a single Peer or Swarm (such as "connect", "close", "get")
    use the currently selected Peer or Swarm as the implicit argument.  The
    "list" command displays a list of Swarms and Peers in an order designed to
    make logical sense to the user, with the currently selected Peer and Swarm
    appearing with an asterisk next to them.  The commands "next" and "prev"
    move the Peer selector to the next and previous Peer in the list, and the
    "NEXT" and "PREV" (capitalized) commands do likewise for the list of
    Swarms.

    Commands may be abbreviated, so long as those abbreviations are unique.
    There's also an aliases dictionary, mostly used to allow some abbreviations
    that would otherwise not be unique -- for example, "p" is aliases to "prev",
    so that it can be abbreviated even though there is also a "pex" command.
    Aliases may not be abbreviated.

    Most commands print an extra newline before returning, and most of the
    others are commands which generate network traffic, for which the newline
    doesn't add much readability.

    The Python GNU Readline bindings don't expose a non-blocking interface,
    so incorporating it into our very nice, singled-threaded EventLoop isn't
    possible.  For now, just read characters directly from stdin, without
    supporting any type of command line editing.

    '''

    aliases = {
                '?':    'help',
                'l':    'list',
                'ls':   'list',
                'p':    'prev',
                'c':    'connect',
            }

    def __repr__(self):
        try:
            return "UserInput(%s)" % self.fd.fileno()
        except:
            return "UserInput(oops, %s)" % id(self)

    def __init__(self):
        self.fd = sys.stdin
        self.readbuf = ''
        self.commands = [ x.replace('cmd_', '', 1)
                          for x in dir(self)
                          if x.startswith('cmd_') ]

        self.swarm_index = 0
        self.swarm = None

        self.peer_order = []
        self.peer_index = 0
        self.peer = None

        EventLoop.register(self)

    def fileno(self):
        return self.fd.fileno()

    def wants_readable(self):
        return True

    def on_readable(self):
        buf = self.fd.readline()
        if buf == '':
            print "stdin EOF"
            sys.exit(0)
            return

        self.command(buf)

    ###

    def update_peer_order(self):
        '''
        Sorts the list of Peers into a logical order to display to the user.
        Also confirms the currently selected Peer and Swarm are still valid, and
        if not, set them to something nearby, as to be the least jarring to the
        user.
        '''

        self.peer_order = []

        # First incldue all Peers associated with a known Swarm
        for swarm in Swarm.list():
            self.peer_order += [ x for x in Peer.list() if x.swarm == swarm ]

        # Next, Peers without a Swarm (e.g., inbound connections)
        self.peer_order += [ x for x in Peer.list() if not x.swarm ]

        # Lastly, Peers with a Swarm defined that don't appear in the list of Swarms (BUG!)
        self.peer_order += [ x for x in Peer.list() if x.swarm and x.swarm not in Swarm.list() ]

        if set(self.peer_order) != set(Peer.list()):
            print "ERROR! Sorted list and Peer list differ:"
            print "   ", Peer.list()
            print "   ", self.peer_order
            assert False

        # If the currently selected Peer is still present in the list of ordered
        # Peers, great!  Make note of its location in the list of ordered peers,
        # so if we lose it, we can select something that was nearby.  If the
        # Peer is no longer valid, do a boundary check, and pick something
        # appropriate.
        if self.peer and self.peer in self.peer_order:
            self.peer_index = self.peer_order.index(self.peer)
        else:
            if self.peer_index >= len(self.peer_order):
                self.peer_index = 0

            if self.peer_index < 0:
                self.peer_index = len(self.peer_order) - 1

            if self.peer_order:
                self.peer = self.peer_order[self.peer_index]

            else:
                self.peer = None

        # Do the same for the currently selected Swarm as we did for the
        # currently selected Peer, above.
        if self.swarm and self.swarm in Swarm.list():
            self.swarm_index = Swarm.list().index(self.swarm)
        else:
            if self.swarm_index >= len(Swarm.list()):
                self.swarm_index = 0

            if self.swarm_index < 0:
                self.swarm_index = len(Swarm.list()) - 1

            if Swarm.list():
                self.swarm = Swarm.list()[self.swarm_index]
            else:
                self.swarm = None

    def command(self, line, raise_=False):
        '''
        Parse a single line of user input.  Returns True if the command was
        successful, or False if there was an error (or if an Exception was
        caught).  If raise_ is true, caught Exceptions are re-raised after
        displaying diagnostic output.
        '''

        args = line.split()
        if not args:
            return

        cmd = self.aliases.get(args[0], args[0])
        args = args[1:]

        # Find all possible valid commands this may be an abbreviation
        # for -- but only if it isn't an exact match.
        if cmd in self.commands:
            possible = [ cmd ]
        else:
            possible = [ x for x in self.commands if x.startswith(cmd) ]

        if not possible:
            print "Unknown command"
            return

        if len(possible) > 1:
            print "Ambiguous command, might be:", possible
            return

        cmd = possible[0]
        fn = 'cmd_' + cmd

        print ">>", cmd, ' '.join(args)

        try:
            self.update_peer_order()
            f = getattr(self, fn)
            ret = f(args)

            # Commands that return False are treated as errors.  All other
            # values (including None) are treated as sucess -- allowing for an
            # implicit return at the end of a Python function to signal success.
            if ret == False:
                return False
            else:
                return True

        except NoPeer:
            print "No active peer"
            return False

        except NoSwarm:
            print "No active swarm"
            return False

        except Exception as e:
            print "Command generated exception"
            traceback.print_exc()
            if raise_:
                raise
            else:
                print
            return False

    def assert_peer(self):
        'Helper function for commands'
        if not self.peer:
            raise NoPeer()

    def assert_swarm(self):
        'Helper function for commands'
        if not self.swarm:
            raise NoSwarm()

    ###

    def cmd_quit(self, args):
        sys.exit(0)

    def cmd_help(self, args):
        print [ x.replace('cmd_', '', 1) for x in self.commands ]
        print

    def cmd_aliases(self, args):
        for k,v in self.aliases.iteritems():
            print "%s: %s" % (k, v)
        print

    ###

    def cmd_exception(self, args):
        raise Exception("Yo!  Exception handling test.")

    def cmd_send_bad_msg(self, args):
        self.assert_peer()
        print "Deliberately sending a bad message"
        self.peer.send('bad-message')

    def cmd_port(self, args):
        if not args:
            print "Usage: <port>"
            return

        if EventLoop.started:
            print "Port cannot be set after EventLoop has started"
            return

        config.listen_port = int(args[0])
        print "Port set to", config.listen_port

    def cmd_peerid(self, args):
        if not args:
            print repr(Peer.my_peerid)
            print
            return

        if args[0] == 'default':
            args[0] = config.default_peerid

        if EventLoop.started and Peer.my_peerid:
            print "PeerID already set to", repr(Peer.my_peerid)
            print
            return False

        else:
            Peer.my_peerid = args[0]
            print "PeerID now", repr(Peer.my_peerid)
            print

    ###

    def cmd_next(self, args):
        self.peer = None
        self.peer_index += 1
        self.cmd_list([])

    def cmd_prev(self, args):
        self.peer = None
        self.peer_index -= 1
        self.cmd_list([])

    def cmd_NEXT(self, args):
        self.swarm = None
        self.swarm_index += 1
        self.cmd_list([])

    def cmd_PREV(self, args):
        self.swarm = None
        self.swarm_index -= 1
        self.cmd_list([])


    ###

    # The following group of incestuous functions implement the "list" command,
    # which attempts to pretty-print the current state of the world.  Could
    # benefit from a bunch of refactoring.

    def show_peer(self, x):
        if x == self.peer:
            selector = '*'
        else:
            selector = ' '

        if not x.connected:
            direction = '==>'
        elif x.inbound and not x.swarm:
            direction = '<=='
        elif x.inbound:
            direction = '<--'
        else:
            direction = '-->'

        if x.remote_peerid:
            peerid = x.remote_peerid
        else:
            peerid = 'Connection in progress'

        if x.cloning:
            cloning = ' (actively cloning)'
        else:
            cloning = ''

        host, port = x.connection.addr
        port = int(port)

        line = '  %s %s %s:%d' % (selector, direction, host, port)
        print line + ' '*(28 - len(line)) + peerid + cloning

    def show_dampen(self, swarm):
        dampen = [ (expire - time.time(), addr) for addr,expire in swarm.dampen.iteritems() ]
        dampen.sort()

        for time_left,addr in dampen:
            if int(time_left) == 1:
                secs = 'second'
            else:
                secs = 'seconds'
            print '    -/- Dampened for %d %s: %s %d' % (time_left, secs, addr[0], addr[1])

        if dampen:
            print

    def list_peers(self):
        self.update_peer_order()

        for swarm in Swarm.list():
            if swarm == self.swarm:
                selector = ' *'
            else:
                selector = ''

            print '%s, %s%s' % (repr(swarm), repr(swarm.directory), selector)

            trackers = [ x for x in Tracker.list() if swarm in x.swarms.keys() ]
            for tracker in Tracker.list():
                val = tracker.swarms.get(swarm, None)
                if val is None:
                    continue

                left = int(val - time.time())

                if val == 0:
                    print "    -T- %s, never contacted" % repr(tracker)
                elif left >= 0:
                    print "    -T- %s, next update in %d seconds" % (repr(tracker), left)
                else:
                    print "    -T- %s, next update was scheduled for %d seconds ago" % (repr(tracker), abs(left))

            if trackers:
                print

            peers = [ x for x in Peer.list() if x.swarm == swarm ]
            if not peers:
                print '    --- No peers'
            for x in peers:
                self.show_peer(x)
            print

            self.show_dampen(swarm)

        inbound = [ x for x in Peer.list() if not x.swarm ]
        if inbound:
            print "Inbound"
            for x in inbound:
                self.show_peer(x)
            print

        if not Swarm.list():
            print "No swarms"
            print

    def cmd_list(self, args):
        print "EventLoop:"
        for x in EventLoop.socks:
            print '   ', x
        print

        print "I am", repr(Peer.my_peerid)
        print

        self.list_peers()

        lost = [ x for x in Peer.list() if x.swarm and x.swarm not in Swarm.list() ]
        if lost:
            print "BUG, LOST PEERS:"
            for x in inbound:
                self.show_peer(x)
            print

    ###


    def cmd_sha(self, args):
        if not Swarm.list():
            print "No swarms"
        for x in Swarm.list():
            print x, x.sha, repr(x.directory)
        print

    def cmd_fetch(self, args):
        self.assert_peer()
        self.peer.on_ref_change(None)

    def cmd_ping(self, args):
        self.assert_peer()
        self.peer.send('ping')

    def cmd_pex(self, args):
        self.assert_peer()
        self.peer.send('pex_request')

    def cmd_close(self, args):
        self.assert_peer()
        self.peer.close()

    def cmd_connect(self, args):
        self.assert_swarm()

        if len(args) == 1:
            ip = '127.0.0.1'
            port = int(args[0])

        elif len(args) == 2:
            ip = args[0]
            port = int(args[1])

        else:
            print "Usage: <ip> <port>, or <port> for localhost"
            return False

        if not validate_ip(ip):
            print "Invalid IP.  Only dotted decimal supported"
            return False

        self.swarm.connect((ip, port))

    def cmd_lpd(self, args):
        self.assert_swarm()
        LPD.update()

    # Useful for testing things like "get config", "get HEAD",
    # "get nonexistant", "get config", "get ../../../../../etc/passwd"
    def cmd_get(self, args):
        self.assert_peer()
        if len(args) != 1:
            print "Usage: <filename>"
            return
        filename = args[0]
        FakeProxy(self.peer, filename)

    # Only useful for doing test with curl(1), or git-fetch(1) directly.
    def cmd_proxy(self, args):
        self.assert_peer()
        ProxyListener(self.peer, 0)

    def cmd_add(self, args):
        if len(args) != 1:
            print "Usage: <directory>"
            return
        directory = os.path.expanduser(args[0])
        self.swarm = Swarm(directory)

    def cmd_clone(self, args):
        if len(args) != 2:
            print "Usage: clone <sha> <directory>"
            return

        sha = args[0]
        directory = os.path.expanduser(args[1])
        Swarm(directory, sha=sha, clone=True)

    def cmd_drop(self, args):
        self.assert_swarm()
        self.swarm.drop()

    def cmd_undampen(self, args):
        self.assert_swarm()
        print "Forgetting", len(self.swarm.dampen), "dampened peers"
        self.swarm.dampen = {}

    # Force the Tracker to re-announce, by setting the last announce time for
    # each Swarm to 0.
    def cmd_updatetracker(serf, args):
        for t in Tracker.list():
            for s in t.swarms.keys():
                print t, s
                t.swarms[s] = 0

    # Deliberately corrupt the Tracker connection ID, for testing
    def cmd_corrupt_connid(self, args):
        for t in Tracker.list():
            t.conn_id = 42
            print t, "connection ID set to 42"

    def cmd_tracker(self, args):
        pass

    def cmd_remotes(self, args):
        for s in Swarm.list():
            print 'Remotes for %s:' % s
            for r in s.git.remotes():
                print '    ', r
