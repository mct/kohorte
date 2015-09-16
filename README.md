# Kohorte

## Overview

Kohorte is a peer-to-peer protocol for sharing git repositories.

Swarms are identified by the SHA of the repository's first commit, and each
remote peer is tracked as a separate `git remote` using a `p2p://p2p-PEERID`
URL scheme.  Changes committed locally are propagated throughout the swarm in
real time.  Given a swarm's SHA, it is possible to clone from the network into
a new directory.

By default, local branches are never modified.  To incorporate a collaborator's
changes, you can `git merge` or `cherry-pick` from a git remote by hand.
Alternatively, if started with the `--auto-merge` option, Kohorte will merge
changes into the current local branch if it can do so with a fast-forward
commit.

Kohorte is in its infancy, and still has a number of rough edges.  It has a
very naive command line interface that reads characters directly from stdin
rather than using GNU Readline (due to problems with the Python Readline
bindings not exposing the non-blocking API), and there is currently no
mechanism to adjust the level of debugging output.  The output is currently
*extremely* verbose.

Planned changes for future releases can be found in the Roadmap, below.

## Security Considerations

With the first public release, there are a number of security concerns to be
aware of.

- There is no encryption.  Future releases will support authenticated encrypting
  using NaCl.  Until then, this means:

  - There is no confidentiality; Anyone snooping network traffic can read your data.
  - There is no authentication; A remote attacker can assume any PeerID they like.
  - There is no integrity; A man-in-the-middle can modify data without your knowledge.

- There is no protection against rollback attacks.

- Kohorte is subject to DOS attacks.  `git fetch` doesn't limits the amount of
  data it will download from a remote.  An attacker can exploit this to consume
  all available bandwidth and disk space.

- Kohorte is susceptible to the problems inherent in every peer-to-peer
  network.  It attempts to find peers via a BitTorrent UDP tracker, and by
  sending Local Peer Discovery (LPD) messages to a link-local multicast
  address.  An attacker monitoring network traffic can identify swarms Kohorte
  is participating in.  Participating in a swarm and publishing your PeerID can
  be used as a unique signature, similar to a global tracking cookie.  A
  malicious tracker can record where Kohorte connects from, over time
  building a profile of users' activity.

- Kohorte does not have any known directory traversal attacks, and limits the
  filenames it will serve from the `.git/` directory, but it may still leak
  more information than it should.  Currently, Kohorte limits the files it will
  serve to `.git/HEAD`, `.git/info/refs` and `.git/objects/*`.  The `refs` file
  contains a list of every git remote (including every remote peer), which may
  contain sensitive data.

## Usage

Swarms to particpate in are specified by the repository's directory.  For
example, to participate in the swarm for its own source code:

```
kohorte ~/src/kohorte
```

Command line options:

```
        usage: kohorte [-h] [--port PORT] [--config CONFIG] [--no-prune]
                       [--auto-merge] [--no-tracker] [--no-lpd] [--lpd-time LPD_TIME]
                       [--lpd-retry LPD_RETRY] [--peerid PEERID] [--add ADD]
                       [--connect CONNECT] [--tracker TRACKER] [-x CMD] [-q]
                       [directory [directory ...]]

        positional arguments:
          directory

        optional arguments:
          -h, --help            show this help message and exit
          --port PORT
          --config CONFIG
          --no-prune            Prevent passing --prune to git fetch
          --auto-merge          Merge to local branch, if fast-forward is possible.
                                DANGEROUS.
          --no-tracker          Do not use the default tracker, tr.iz.is:6969
          --no-lpd              Disable multicast Local Peer Discovery
          --lpd-time LPD_TIME   How often to send LPD messages
          --lpd-retry LPD_RETRY
                                Time to wait between LPD retries, after socket error
          --peerid PEERID
          --add ADD             Add a swarm by specifying its directory
          --connect CONNECT     Connect to a peer at IP:port
          --tracker TRACKER     Specify a tracker, other than the default
          -x CMD, --cmd CMD     Execute a CLI command
          -q, --no-rc           Do not read ~/.kohorte
```

## CLI

Kohorte has a very simple command line interface, reading data directly from
stdin, and does not support any form command line editing.  Because Kohorte
uses a single-threaded, non-blocking event loop, and the Python bindings for
GNU Readline don't expose the non-blocking API, adding Readline suport is
non-trivial.

Commands may be abbreviated, so long the abbreviations are unique.

Supported Commands:

- **`quit`**  Exits

- **`list`**  Displays a general summary of the current state, including a list
  of Python objects in the EventLoop, the local PeerID, a list of Swarms, and
  the Peers associated with each Swarm.  The currently selected Peer and Swarm
  is displayed with an asterisk.

- **`next`**, **`prev`**  Change the currently selected Peer to the next or
  previous peer, as they appear in the **`list`** output.  May be abbreviated
  as **`n`** and **`p`**

- **`NEXT`**, **`PREV`**  Change the currently selected Swarm to the next or
  previous Swarm

- **`connect`**  Connect to a Peer at a known IP and port

- **`close`**  Close the currently selected Peer

- **`fetch`**  Manually start a `git fetch` against the selected Peer

- **`sha`**  Display the full SHA of each Swarm

- **`add`**  Add a new Swarm

- **`drop`**  Remove the currently selected Swarm, and close all of its
  associated peers

- **`clone`**  Clone a new git repository from the given Swarm's SHA into the
  specified directory.  A clone operation will only download data from the
  first Peer it can connect to.

For debugging:

- **`get`**  Request a file from the currently selected Peer.  The file's
  contents will appear in the debugging output.

- **`proxy`**  Manually start an HTTP proxy.  Normally only created for each
  git operation, and destroyed once the git command finishes.  This will start
  a long-lived proxy, which you can connect to with, e.g., curl.  The proxy URL
  will be printed to the debugging output, which includes the port and
  authentication information.

- **`undampen`**  Purge the list of dampened addresses.

- **`lpd`**  Force and Local Peer Discovery multicast message to be sent

- **`pex`**  Peer Exchange.  Ask the remote peer to send a list of its
  currently connected peers.

- **`updatetracker`**  Immediately request more peers from each tracker.

## Use Cases

- Most obvious, the ability to share data without a canonical, central
  repository.  You can quickly start collaborating with a peer without first
  having to configure a git remote you can both reach and authenticate to.

- With `auto-merge` enabled, and a process auto-committing changes regularly,
  Kohorte could be used as a peer-to-peer, DropBox-style synchronization tool.
  As a side effect, a full revision history is preserved for each file.

- Without `auto-merge` enabled, and a process auto-committing infrequently,
  Kohorte can be used as a distributed backup system.

## Limitations

- Kohorte currently only supports TCP, which make it difficult to use through a
  NAT device or firewall.  Future versions will use uTP (uTorrent Transfer
  Protocol).

- Kohorte currently requires a full mesh &mdash; i.e., Kohorte will only
  exchange data with a peer it is directly connected to.  If Alice can talk to
  Bob, and Bob can talk to Carol, but Alice and Carol cannot talk directly to
  each other, then Alice and Carol will not learn of each other's commits.

## Technical Details

The Kohorte protocol is composed of Messages.  Valid messages are defined in
the `MessageTypes` dictionary in `messages.py`,  and appear on the wire as
bencoded dictionaries prefixed with a length field of four ASCII hexadecimal
characters.  For human readability, optional whitespace is permitted both
before and after the length field and the bencoded blob.  For example, a `ping`
message may appear on the wire as `000d d3:msg4:pinge\r\n`

Kohorte interacts with git by providing an HTTP proxy that translates GET
requests into file requests from a remote peer using the Kohorte protocol.

To handle the `p2p` URL scheme, `git fetch` looks for a remote helper program
named `git-remote-p2p` &mdash; a shell script provided by Kohorte that massages
the URL, sets the `http_proxy` environment variable to point to the Kohorte
proxy, and execs `git-remote-http`.  `git-remote-http` is unaware it is
speaking with a remote Kohorte peer.

Kohorte requires a full mesh because git doesn't provide a mechanism for `git
fetch` to retrieve a remote peer's list of remote refs.  It should be possible
to work around this by further massaging the HTTP requests, but doing so will
require keeping a good deal more state.

Kohorte is implemented using a single-threaded, custom event loop.

## Roadmap to 1.0

A list of milestones and dot releases to reach Version 1.0.  Development will
take place in the `mct-dev` branch, rebased into `master` for each dot release.
`master` will always be releasable, although there may be protocol
incompatibilities between dot releases.

- **Version 0.1**

  - Initial public release!  Rejoice!


- **Version 0.2**

  - Add `set` and `show` commands to modify variables in the `config` module

  - Persistent peers &mdash; a list of `{IP, port, PeerID}` tuples to connect to on a
    regular basis, if there is no existing connection to `PeerID`.

  - Fix the idle timeout logic

  - The `PeerConnection writebuf` logic may not be using `SO_SNDBUF` as intended

  - Stagger pings, LPD announcements, and outbound connection attempts.


- **Version 0.3**

  - Migrate to a real logging system, where debugging output for individual
    components can be turned up and down.

  - Send the contents of `git show-ref`, so a remote peer can decide if it
    needs to do a `git fetch`, rather than always doing one on connect.

  - Monotonic time


- **Version 0.4**

  - Support ephemeral Curve25519, to provide confidentiality and integrity, but
    not authentication.


- **Version 0.5**

  - Shared key authentication


- **Version 0.6**

  - Trust on First Use (TOFO) verification of PeerID


- **Version 0.7**

  - CA-style authentication, with Ed25519 keys


- **Version 0.8**

  - Asynchronous DNS resolution, perhaps using https://code.google.com/p/adns-python/

  - Support connecting to remote peers by hostname, not just IP address.


- **Version 0.9**

  - SOCKS4 proxy support for outbound connections

  - ssh-style ProxyCommand


- **Version 0.10**

  - uTP support


- **Version 1.0**

  - **First stable release!  Rejoice!**

  - Should be usable by intermediate git users.


- **Post 1.0**

  - Remove the requirement for a full mesh

  - Signed peer changes, once a full mesh is no longer required

  - A better CLI.  Perhaps using something other than GNU Readline, perhaps
    spawning a single thread for Readline, or perhaps using an IRC-like
    external front-end.


## Requirements

- A Unix-like operating system.  Only Linux has been tested.

- The bencode Python library.

- A version of git new enough to support `-C`.  For Debian Wheezy, the version
  in `wheezy-backports` is recent enough.

## Copyright Information

Copyright &copy; 2015, Michael Toren &lt;kohorte@toren.net&gt;

Released under the terms of the GNU GPL, version 2

<!---
vim:set ts=8 sw=8 ai et nowrap:
--->
