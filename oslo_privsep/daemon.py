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

'''Privilege separation ("privsep") daemon.

To ease transition this supports 2 alternative methods of starting the
daemon, all resulting in a helper process running with elevated
privileges and open socket(s) to the original process:

1. Start via fork()

   Assumes process currently has all required privileges and is about
   to drop them (perhaps by setuid to an unprivileged user).  If the
   the initial environment is secure and `PrivContext.start(Method.FORK)`
   is called early in `main()`, then this is the most secure and
   simplest.  In particular, if the initial process is already running
   as non-root (but with sufficient capabilities, via eg suitable
   systemd service files), then no part needs to involve uid=0 or
   sudo.

2. Start via sudo/rootwrap

   This starts the privsep helper on first use via sudo and rootwrap,
   and communicates via a temporary Unix socket passed on the command
   line.  The communication channel is briefly exposed in the
   filesystem, but is protected with file permissions and connecting
   to it only grants access to the unprivileged process.  Requires a
   suitable entry in sudoers or rootwrap.conf filters.

The privsep daemon exits when the communication channel is closed,
(which usually occurs when the unprivileged process exits).

'''

import enum
import errno
import io
import logging as pylogging
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading

if platform.system() == 'Linux':
    import fcntl
    import grp
    import pwd

import eventlet
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import importutils

from oslo_privsep._i18n import _
from oslo_privsep import capabilities
from oslo_privsep import comm


LOG = logging.getLogger(__name__)


@enum.unique
class StdioFd(enum.IntEnum):
    # NOTE(gus): We can't use sys.std*.fileno() here.  sys.std*
    # objects may be random file-like objects that may not match the
    # true system std* fds - and indeed may not even have a file
    # descriptor at all (eg: test fixtures that monkey patch
    # fixtures.StringStream onto sys.stdout).  Below we always want
    # the _real_ well-known 0,1,2 Unix fds during os.dup2
    # manipulation.
    STDIN = 0
    STDOUT = 1
    STDERR = 2


@enum.unique
class Message(enum.IntEnum):
    """Types of messages sent across the communication channel"""
    PING = 1
    PONG = 2
    CALL = 3
    RET = 4
    ERR = 5
    LOG = 6


class FailedToDropPrivileges(Exception):
    pass


class ProtocolError(Exception):
    pass


def set_cloexec(fd):
    flags = fcntl.fcntl(fd, fcntl.F_GETFD)
    if (flags & fcntl.FD_CLOEXEC) == 0:
        flags |= fcntl.FD_CLOEXEC
        fcntl.fcntl(fd, fcntl.F_SETFD, flags)


def setuid(user_id_or_name):
    try:
        new_uid = int(user_id_or_name)
    except (TypeError, ValueError):
        new_uid = pwd.getpwnam(user_id_or_name).pw_uid
    if new_uid != 0:
        try:
            os.setuid(new_uid)
        except OSError:
            msg = _('Failed to set uid %s') % new_uid
            LOG.critical(msg)
            raise FailedToDropPrivileges(msg)


def setgid(group_id_or_name):
    try:
        new_gid = int(group_id_or_name)
    except (TypeError, ValueError):
        new_gid = grp.getgrnam(group_id_or_name).gr_gid
    if new_gid != 0:
        try:
            os.setgid(new_gid)
        except OSError:
            msg = _('Failed to set gid %s') % new_gid
            LOG.critical(msg)
            raise FailedToDropPrivileges(msg)


class PrivsepLogHandler(pylogging.Handler):
    def __init__(self, channel, processName=None):
        super(PrivsepLogHandler, self).__init__()
        self.channel = channel
        self.processName = processName

    def emit(self, record):
        # Vaguely based on pylogging.handlers.SocketHandler.makePickle

        if self.processName:
            record.processName = self.processName

        data = dict(record.__dict__)

        if record.exc_info:
            if not record.exc_text:
                fmt = self.formatter or pylogging.Formatter()
                data['exc_text'] = fmt.formatException(record.exc_info)
            data['exc_info'] = None  # drop traceback in favor of exc_text

        # serialise msg now so we can drop (potentially unserialisable) args
        data['msg'] = record.getMessage()
        data['args'] = ()

        self.channel.send((None, (Message.LOG, data)))


class _ClientChannel(comm.ClientChannel):
    """Our protocol, layered on the basic primitives in comm.ClientChannel"""

    def __init__(self, sock):
        super(_ClientChannel, self).__init__(sock)
        self.exchange_ping()

    def exchange_ping(self):
        try:
            # exchange "ready" messages
            reply = self.send_recv((Message.PING.value,))
            success = reply[0] == Message.PONG
        except Exception as e:
            LOG.exception('Error while sending initial PING to privsep: %s', e)
            success = False
        if not success:
            msg = _('Privsep daemon failed to start')
            LOG.critical(msg)
            raise FailedToDropPrivileges(msg)

    def remote_call(self, name, args, kwargs):
        result = self.send_recv((Message.CALL.value, name, args, kwargs))
        if result[0] == Message.RET:
            # (RET, return value)
            return result[1]
        elif result[0] == Message.ERR:
            # (ERR, exc_type, args)
            #
            # TODO(gus): see what can be done to preserve traceback
            # (without leaking local values)
            exc_type = importutils.import_class(result[1])
            raise exc_type(*result[2])
        else:
            raise ProtocolError(_('Unexpected response: %r') % result)

    def out_of_band(self, msg):
        if msg[0] == Message.LOG:
            # (LOG, LogRecord __dict__)
            record = pylogging.makeLogRecord(msg[1])
            if LOG.isEnabledFor(record.levelno):
                LOG.logger.handle(record)
        else:
            LOG.warning('Ignoring unexpected OOB message from privileged '
                        'process: %r', msg)


def fdopen(fd, *args, **kwargs):
    # NOTE(gus): We can't just use os.fdopen() here and allow the
    # regular (optional) monkey_patching to do its thing.  Turns out
    # that regular file objects (as returned by os.fdopen) on python2
    # are broken in lots of ways regarding blocking behaviour.  We
    # *need* the newer io.* objects on py2 (doesn't matter on py3,
    # since the old file code has been replaced with io.*)
    if eventlet.patcher.is_monkey_patched('socket'):
        return eventlet.greenio.GreenPipe(fd, *args, **kwargs)
    else:
        return io.open(fd, *args, **kwargs)


def _fd_logger(level=logging.WARN):
    """Helper that returns a file object that is asynchronously logged"""
    read_fd, write_fd = os.pipe()
    read_end = fdopen(read_fd, 'r', 1)
    write_end = fdopen(write_fd, 'w', 1)

    def logger(f):
        for line in f:
            LOG.log(level, 'privsep log: %s', line.rstrip())
    t = threading.Thread(
        name='fd_logger',
        target=logger, args=(read_end,)
    )
    t.daemon = True
    t.start()

    return write_end


def replace_logging(handler, log_root=None):
    if log_root is None:
        log_root = logging.getLogger(None).logger  # root logger
    for h in log_root.handlers:
        log_root.removeHandler(h)
    log_root.addHandler(handler)


class ForkingClientChannel(_ClientChannel):
    def __init__(self, context):
        """Start privsep daemon using fork()

        Assumes we already have required privileges.
        """

        sock_a, sock_b = socket.socketpair()

        for s in (sock_a, sock_b):
            s.setblocking(True)
            # Important that these sockets don't get leaked
            set_cloexec(s)

        # Try to prevent any buffered output from being written by both
        # parent and child.
        for f in (sys.stdout, sys.stderr):
            f.flush()

        if os.fork() == 0:
            # child

            channel = comm.ServerChannel(sock_b)
            sock_a.close()

            # Replace root logger early (to capture any errors during setup)
            replace_logging(PrivsepLogHandler(channel,
                                              processName=str(context)))

            Daemon(channel, context=context).run()
            LOG.debug('privsep daemon exiting')
            os._exit(0)

        # parent

        sock_b.close()
        super(ForkingClientChannel, self).__init__(sock_a)


class RootwrapClientChannel(_ClientChannel):
    def __init__(self, context):
        """Start privsep daemon using exec()

        Uses sudo/rootwrap to gain privileges.
        """

        listen_sock = socket.socket(socket.AF_UNIX)

        # Note we listen() on the unprivileged side, and connect to it
        # from the privileged process.  This means there is no exposed
        # attack point on the privileged side.

        # NB: Permissions on sockets are not checked on some (BSD) Unices
        # so create socket in a private directory for safety.  Privsep
        # daemon will (initially) be running as root, so will still be
        # able to connect to sock path.
        tmpdir = tempfile.mkdtemp()  # NB: created with 0700 perms

        try:
            sockpath = os.path.join(tmpdir, 'privsep.sock')
            listen_sock.bind(sockpath)
            listen_sock.listen(1)

            cmd = context.helper_command(sockpath)
            LOG.info('Running privsep helper: %s', cmd)
            proc = subprocess.Popen(cmd, shell=False, stderr=_fd_logger())
            if proc.wait() != 0:
                msg = ('privsep helper command exited non-zero (%s)' %
                       proc.returncode)
                LOG.critical(msg)
                raise FailedToDropPrivileges(msg)
            LOG.info('Spawned new privsep daemon via rootwrap')

            sock, _addr = listen_sock.accept()
            LOG.debug('Accepted privsep connection to %s', sockpath)

        finally:
            # Don't need listen_sock anymore, so clean up.
            listen_sock.close()
            try:
                os.unlink(sockpath)
            except OSError as e:
                if e.errno != errno.ENOENT:
                    raise
            os.rmdir(tmpdir)

        super(RootwrapClientChannel, self).__init__(sock)


class Daemon(object):
    """NB: This doesn't fork() - do that yourself before calling run()"""

    def __init__(self, channel, context):
        self.channel = channel
        self.context = context
        self.user = context.conf.user
        self.group = context.conf.group
        self.caps = set(context.conf.capabilities)

    def run(self):
        """Run request loop. Sets up environment, then calls loop()"""
        os.chdir("/")
        os.umask(0)
        self._drop_privs()
        self._close_stdio()

        self.loop()

    def _close_stdio(self):
        with open(os.devnull, 'w+') as devnull:
            os.dup2(devnull.fileno(), StdioFd.STDIN)
            os.dup2(devnull.fileno(), StdioFd.STDOUT)
            # stderr is left untouched

    def _drop_privs(self):
        try:
            # Keep current capabilities across setuid away from root.
            capabilities.set_keepcaps(True)

            if self.group is not None:
                try:
                    os.setgroups([])
                except OSError:
                    msg = _('Failed to remove supplemental groups')
                    LOG.critical(msg)
                    raise FailedToDropPrivileges(msg)

            if self.user is not None:
                setuid(self.user)

            if self.group is not None:
                setgid(self.group)

        finally:
            capabilities.set_keepcaps(False)

        LOG.info('privsep process running with uid/gid: %(uid)s/%(gid)s',
                 {'uid': os.getuid(), 'gid': os.getgid()})

        capabilities.drop_all_caps_except(self.caps, self.caps, [])

        def fmt_caps(capset):
            if not capset:
                return 'none'
            fc = [capabilities.CAPS_BYVALUE.get(c, str(c))
                  for c in capset]
            fc.sort()
            return '|'.join(fc)

        eff, prm, inh = capabilities.get_caps()
        LOG.info(
            'privsep process running with capabilities '
            '(eff/prm/inh): %(eff)s/%(prm)s/%(inh)s',
            {
                'eff': fmt_caps(eff),
                'prm': fmt_caps(prm),
                'inh': fmt_caps(inh),
            })

    def _process_cmd(self, cmd, *args):
        if cmd == Message.PING:
            return (Message.PONG.value,)

        elif cmd == Message.CALL:
            name, f_args, f_kwargs = args
            func = importutils.import_class(name)

            if not self.context.is_entrypoint(func):
                msg = _('Invalid privsep function: %s not exported') % name
                raise NameError(msg)

            ret = func(*f_args, **f_kwargs)
            return (Message.RET.value, ret)

        raise ProtocolError(_('Unknown privsep cmd: %s') % cmd)

    def loop(self):
        """Main body of daemon request loop"""
        LOG.info('privsep daemon running as pid %s', os.getpid())

        # We *are* this context now - any calls through it should be
        # executed locally.
        self.context.set_client_mode(False)

        for msgid, msg in self.channel:
            LOG.debug('privsep: request[%(msgid)s]: %(req)s',
                      {'msgid': msgid, 'req': msg})
            try:
                reply = self._process_cmd(*msg)
            except Exception as e:
                LOG.debug(
                    'privsep: Exception during request[%(msgid)s]: %(err)s',
                    {'msgid': msgid, 'err': e}, exc_info=True)
                cls = e.__class__
                cls_name = '%s.%s' % (cls.__module__, cls.__name__)
                reply = (Message.ERR.value, cls_name, e.args)

            try:
                LOG.debug('privsep: reply[%(msgid)s]: %(reply)s',
                          {'msgid': msgid, 'reply': reply})
                self.channel.send((msgid, reply))
            except IOError as e:
                if e.errno == errno.EPIPE:
                    # Write stream closed, exit loop
                    break
                raise

        LOG.debug('Socket closed, shutting down privsep daemon')


def helper_main():
    """Start privileged process, serving requests over a Unix socket."""

    cfg.CONF.register_cli_opts([
        cfg.StrOpt('privsep_context', required=True),
        cfg.StrOpt('privsep_sock_path', required=True),
    ])

    logging.register_options(cfg.CONF)

    cfg.CONF(args=sys.argv[1:], project='privsep')
    logging.setup(cfg.CONF, 'privsep')  # note replace_logging call below

    context = importutils.import_class(cfg.CONF.privsep_context)
    from oslo_privsep import priv_context   # Avoid circular import
    if not isinstance(context, priv_context.PrivContext):
        LOG.fatal('--privsep_context must be the (python) name of a '
                  'PrivContext object')

    sock = socket.socket(socket.AF_UNIX)
    sock.connect(cfg.CONF.privsep_sock_path)
    set_cloexec(sock)
    channel = comm.ServerChannel(sock)

    # Channel is set up, so fork off daemon "in the background" and exit
    if os.fork() != 0:
        # parent
        return

    # child

    # Note we don't move into a new process group/session like a
    # regular daemon might, since we _want_ to remain associated with
    # the originating (unprivileged) process.

    # Channel is set up now, so move to in-band logging
    replace_logging(PrivsepLogHandler(channel))

    LOG.info('privsep daemon starting')

    try:
        Daemon(channel, context).run()
    except Exception as e:
        LOG.exception(e)
        sys.exit(str(e))

    LOG.debug('privsep daemon exiting')
    sys.exit(0)


if __name__ == '__main__':
    helper_main()
