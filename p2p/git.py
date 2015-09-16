#!/usr/bin/env python
# vim:set ts=4 sw=4 ai et:

# Kohorte, a peer-to-peer protocol for sharing git repositories
# Copyright (c) 2015, Michael Toren <kohorte@toren.net>
# Released under the terms of the GNU GPL, version 2

import sys
import os
import re

from subprocess import Popen, PIPE, check_call

from util import *

class Git(object):
    '''
    A class for working with a Git repository.
    '''

    def __repr__(self):
        try:
            return "Git(%s)" % self.directory
        except:
            return "Git(oops, %s)" % id(self)

    def __init__(self, directory):
        self.directory = directory
        self.history = {}

        try:
            stat = os.stat(self.directory + "/.git/")
        except OSError:
            raise Exception("%s is not a top-level git project" % directory)

        self.get_history()
        self.get_root()

    def get_history(self):
        '''
        Fills in the self.history dict, mapping commits to a list of parents
        '''

        p = Popen(['git', '-C', self.directory, 'log', '--pretty=%H %P'], bufsize=1024*16, stdout=PIPE)

        for line in p.stdout:
            parents = line.split()
            commit = parents.pop(0)
            self.history[commit] = parents

        p.wait()
        if p.returncode != 0:
            raise Exception('git log returned %d' % p.returncode)

    def get_root(self):
        '''
        Looks through self.history and finds the very first commit in the
        repository, identified by a commit with no parents.  We expect there
        to be only one, but if more than one is found, return the first, as
        lexically ordered.
        '''
        candidates = [ x for x in self.history if self.history[x] == [] ]
        candidates.sort()

        if not candidates:
            self.root = None
        else:
            self.root = candidates[0]

        if len(candidates) > 1:
            print timestamp(), self, 'Warning: More than 1 root found? Selecting', self.root

    def update_server_info(self):
        '''
        Runs 'git update-server-info', which updates static files in the .git
        directory used by the 'dumb' Git transfer protocols, such as HTTP.
        
        For more information, see
        https://git-scm.com/book/en/v2/Git-Internals-Transfer-Protocols#The-Dumb-Protocol
        '''

        #print timestamp(), self, "git update-server-info"
        check_call(['git', '-C', self.directory, 'update-server-info'])

    def remotes(self):
        '''
        Returns a list of git remotes
        '''
        out = []
        p = Popen(['git', '-C', self.directory, 'remote'], bufsize=1024*16, stdout=PIPE)
        for line in p.stdout:
            line = line.rstrip()
            out.append(line)

        p.wait()
        if p.returncode != 0:
            raise Exception('git remote returned %d' % p.returncode)

        return out

    def add_remote(self, name):
        '''
        Adds a git remote, if it does not exist
        '''
        remotes = self.remotes()
        name = 'p2p-%s' % name
        url = 'p2p://%s' % name

        if name in remotes:
            return

        cmd = ['git', '-C', self.directory, 'remote', 'add', name, url]
        print timestamp(), self, 'Running', repr(cmd)
        check_call(cmd)

    def refs(self):
        '''
        Returns a dict mapping git refs to commits
        '''
        out = {}
        p = Popen(['git', '-C', self.directory, 'show-ref'], bufsize=1024*16, stdout=PIPE)
        for line in p.stdout:
            commit, ref = line.split()
            out[ref] = commit
        return out

    def refs_signature(self):
        '''
        Returns a string which can be used as a 'signature' of sorts.  It can
        be compared to previous signature to determine if any refs have
        changed.
        '''
        sig = [ x + ':' + y for x,y in self.local_refs().items() ]
        sig.sort()
        return ':'.join(sig)

    def branch(self):
        '''
        Returns the name of the current branch
        '''
        p = Popen(['git', '-C', self.directory, 'symbolic-ref', 'HEAD'], stdout=PIPE)
        line = p.stdout.read()
        p.wait()
        match = re.match(r'^refs/heads/(\S+)$', line)
        if match and len(match.groups()) == 1:
            return match.groups()[0]
        else:
            return None

    def local_refs(self):
        return { key:val for key,val
                 in self.refs().items()
                 if key.startswith('refs/heads/') }

if __name__ == '__main__':
    if len(sys.argv) != 2:
        print "Usage: %s <directory>" % sys.argv[0]
        sys.exit(1)

    git = Git(sys.argv[1])

    print "local refs:"
    for k,v in git.local_refs().iteritems():
        print "  ", v, k
    print

    print "remotes:"
    for x in git.remotes():
        print "  ", x
    print

    print "root:"
    print "  ", git.root
    print

    print "sig:"
    print "  ", git.refs_signature()
    print

    print "branch:", git.branch()
