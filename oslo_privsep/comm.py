# Copyright 2015 Rackspace Inc.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Serialization/Deserialization for privsep.

The wire format is a stream of msgpack objects encoding primitive
python datatypes.  Msgpack 'raw' is assumed to be a valid utf8 string
(msgpack 2.0 'bin' type is used for bytes).  Python lists are
converted to tuples during serialization/deserialization.
"""

import datetime
import enum
import logging
import socket
import sys
import threading

import msgpack
import six

from oslo_privsep._i18n import _
from oslo_utils import uuidutils

LOG = logging.getLogger(__name__)


@enum.unique
class Message(enum.IntEnum):
    """Types of messages sent across the communication channel"""
    PING = 1
    PONG = 2
    CALL = 3
    RET = 4
    ERR = 5
    LOG = 6


class PrivsepTimeout(Exception):
    pass


class Serializer(object):
    def __init__(self, writesock):
        self.writesock = writesock

    def send(self, msg):
        buf = msgpack.packb(msg, use_bin_type=True,
                            unicode_errors='surrogateescape')
        self.writesock.sendall(buf)

    def close(self):
        # Hilarious. `socket._socketobject.close()` doesn't actually
        # call `self._sock.close()`.  Oh well, we really wanted a half
        # close anyway.
        self.writesock.shutdown(socket.SHUT_WR)


class Deserializer(six.Iterator):
    def __init__(self, readsock):
        self.readsock = readsock
        self.unpacker = msgpack.Unpacker(use_list=False, raw=False,
                                         strict_map_key=False,
                                         unicode_errors='surrogateescape')

    def __iter__(self):
        return self

    def __next__(self):
        while True:
            try:
                return next(self.unpacker)
            except StopIteration:
                try:
                    buf = self.readsock.recv(4096)
                    if not buf:
                        raise
                    self.unpacker.feed(buf)
                except socket.timeout:
                    pass


class Future(object):
    """A very simple object to track the return of a function call"""

    def __init__(self, lock, timeout=None):
        self.condvar = threading.Condition(lock)
        self.error = None
        self.data = None
        self.timeout = timeout

    def set_result(self, data):
        """Must already be holding lock used in constructor"""
        self.data = data
        self.condvar.notify()

    def set_exception(self, exc):
        """Must already be holding lock used in constructor"""
        self.error = exc
        self.condvar.notify()

    def result(self):
        """Must already be holding lock used in constructor"""
        before = datetime.datetime.now()
        if not self.condvar.wait(timeout=self.timeout):
            now = datetime.datetime.now()
            LOG.warning('Timeout while executing a command, timeout: %s, '
                        'time elapsed: %s', self.timeout,
                        (now - before).total_seconds())
            return (Message.ERR.value,
                    '%s.%s' % (PrivsepTimeout.__module__,
                               PrivsepTimeout.__name__),
                    '')
        if self.error is not None:
            raise self.error
        return self.data


class ClientChannel(object):
    def __init__(self, sock):
        self.running = False
        self.writer = Serializer(sock)
        self.lock = threading.Lock()
        self.reader_thread = threading.Thread(
            name='privsep_reader',
            target=self._reader_main,
            args=(Deserializer(sock),),
        )
        self.reader_thread.daemon = True
        self.outstanding_msgs = {}

        self.reader_thread.start()

    def _reader_main(self, reader):
        """This thread owns and demuxes the read channel"""
        with self.lock:
            self.running = True
        for msg in reader:
            msgid, data = msg
            if msgid is None:
                self.out_of_band(data)
            else:
                with self.lock:
                    if msgid not in self.outstanding_msgs:
                        LOG.warning("msgid should be in oustanding_msgs, it is"
                                    "possible that timeout is reached!")
                        continue
                    self.outstanding_msgs[msgid].set_result(data)

        # EOF.  Perhaps the privileged process exited?
        # Send an IOError to any oustanding waiting readers.  Assuming
        # the write direction is also closed, any new writes should
        # get an immediate similar error.
        LOG.debug('EOF on privsep read channel')

        exc = IOError(_('Premature eof waiting for privileged process'))
        with self.lock:
            for mbox in self.outstanding_msgs.values():
                mbox.set_exception(exc)
            self.running = False

    def out_of_band(self, msg):
        """Received OOB message. Subclasses might want to override this."""
        pass

    def send_recv(self, msg, timeout=None):
        myid = uuidutils.generate_uuid()
        while myid in self.outstanding_msgs:
            LOG.warning("myid shoudn't be in outstanding_msgs.")
            myid = uuidutils.generate_uuid()
        future = Future(self.lock, timeout)

        with self.lock:
            self.outstanding_msgs[myid] = future
            try:
                self.writer.send((myid, msg))

                reply = future.result()
            except Exception:
                LOG.warning("Unexpected error: {}".format(sys.exc_info()[0]))
                raise
            finally:
                del self.outstanding_msgs[myid]

        return reply

    def close(self):
        with self.lock:
            self.writer.close()

        self.reader_thread.join()


class ServerChannel(six.Iterator):
    """Server-side twin to ClientChannel"""

    def __init__(self, sock):
        self.rlock = threading.Lock()
        self.reader_iter = iter(Deserializer(sock))
        self.wlock = threading.Lock()
        self.writer = Serializer(sock)

    def __iter__(self):
        return self

    def __next__(self):
        with self.rlock:
            return next(self.reader_iter)

    def send(self, msg):
        with self.wlock:
            self.writer.send(msg)
