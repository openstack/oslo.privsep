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


import enum
import functools
import logging
import shlex
import sys

from oslo_config import cfg
from oslo_config import types
from oslo_utils import importutils

from oslo_privsep._i18n import _
from oslo_privsep import capabilities
from oslo_privsep import daemon


LOG = logging.getLogger(__name__)


def CapNameOrInt(value):
    value = str(value).strip()
    try:
        return capabilities.CAPS_BYNAME[value]
    except KeyError:
        return int(value)


OPTS = [
    cfg.StrOpt('user',
               help=_('User that the privsep daemon should run as.')),
    cfg.StrOpt('group',
               help=_('Group that the privsep daemon should run as.')),
    cfg.Opt('capabilities',
            type=types.List(CapNameOrInt), default=[],
            help=_('List of Linux capabilities retained by the privsep '
                   'daemon.')),
    cfg.StrOpt('helper_command',
               help=_('Command to invoke to start the privsep daemon if '
                      'not using the "fork" method. '
                      'If not specified, a default is generated using '
                      '"sudo privsep-helper" and arguments designed to '
                      'recreate the current configuration. '
                      'This command must accept suitable --privsep_context '
                      'and --privsep_sock_path arguments.')),
]

_ENTRYPOINT_ATTR = 'privsep_entrypoint'
_HELPER_COMMAND_PREFIX = ['sudo']


@enum.unique
class Method(enum.Enum):
    FORK = 1
    ROOTWRAP = 2


def init(root_helper=None):
    """Initialise oslo.privsep library.

    This function should be called at the top of main(), after the
    command line is parsed, oslo.config is initialised and logging is
    set up, but before calling any privileged entrypoint, changing
    user id, forking, or anything else "odd".

    :param root_helper: List of command and arguments to prefix
        privsep-helper with, in order to run helper as root.  Note,
        ignored if context's helper_command config option is set.
    """

    if root_helper:
        global _HELPER_COMMAND_PREFIX
        _HELPER_COMMAND_PREFIX = root_helper


class PrivContext(object):
    def __init__(self, prefix, cfg_section='privsep', pypath=None,
                 capabilities=None):

        # Note that capabilities=[] means retaining no capabilities
        # and leaves even uid=0 with no powers except being able to
        # read/write to the filesystem as uid=0.  This might be what
        # you want, but probably isn't.
        #
        # There is intentionally no way to say "I want all the
        # capabilities."
        if capabilities is None:
            raise ValueError('capabilities is a required parameter')

        self.pypath = pypath
        self.prefix = prefix
        self.cfg_section = cfg_section

        # NOTE(claudiub): oslo.privsep is not currently supported on Windows,
        # as it uses Linux-specific functionality (os.fork, socker.AF_UNIX).
        # The client_mode should be set to False on Windows.
        self.client_mode = sys.platform != 'win32'
        self.channel = None

        cfg.CONF.register_opts(OPTS, group=cfg_section)
        cfg.CONF.set_default('capabilities', group=cfg_section,
                             default=capabilities)

    @property
    def conf(self):
        """Return the oslo.config section object as lazily as possible."""
        # Need to avoid looking this up before oslo_config has been
        # properly initialized.
        return cfg.CONF[self.cfg_section]

    def __repr__(self):
        return 'PrivContext(cfg_section=%s)' % self.cfg_section

    def helper_command(self, sockpath):
        # We need to be able to reconstruct the context object in the new
        # python process we'll get after rootwrap/sudo.  This means we
        # need to construct the context object and store it somewhere
        # globally accessible, and then use that python name to find it
        # again in the new python interpreter.  Yes, it's all a bit
        # clumsy, and none of it is required when using the fork-based
        # alternative above.
        # These asserts here are just attempts to catch errors earlier.
        # TODO(gus): Consider replacing with setuptools entry_points.
        if self.pypath is None:
            raise AssertionError('helper_command requires priv_context '
                                 'pypath to be specified')
        if importutils.import_class(self.pypath) is not self:
            raise AssertionError('helper_command requires priv_context '
                                 'pypath for context object')

        # Note order is important here.  Deployments will (hopefully)
        # have the exact arguments in sudoers/rootwrap configs and
        # reordering args will break configs!

        if self.conf.helper_command:
            cmd = shlex.split(self.conf.helper_command)
        else:
            cmd = _HELPER_COMMAND_PREFIX + ['privsep-helper']

            try:
                for cfg_file in cfg.CONF.config_file:
                    cmd.extend(['--config-file', cfg_file])
            except cfg.NoSuchOptError:
                pass

            try:
                if cfg.CONF.config_dir is not None:
                    for cfg_dir in cfg.CONF.config_dir:
                        cmd.extend(['--config-dir', cfg_dir])
            except cfg.NoSuchOptError:
                pass

        cmd.extend(
            ['--privsep_context', self.pypath,
             '--privsep_sock_path', sockpath])

        return cmd

    def set_client_mode(self, enabled):
        if enabled and sys.platform == 'win32':
            raise RuntimeError(
                "Enabling the client_mode is not currently "
                "supported on Windows.")
        self.client_mode = enabled

    def entrypoint(self, func):
        """This is intended to be used as a decorator."""

        if not func.__module__.startswith(self.prefix):
            raise AssertionError('%r entrypoints must be below "%s"' %
                                 (self, self.prefix))

        # Right now, we only track a single context in
        # _ENTRYPOINT_ATTR.  This could easily be expanded into a set,
        # but that will increase the memory overhead.  Revisit if/when
        # someone has a need to associate the same entrypoint with
        # multiple contexts.
        if getattr(func, _ENTRYPOINT_ATTR, None) is not None:
            raise AssertionError('%r is already associated with another '
                                 'PrivContext' % func)

        f = functools.partial(self._wrap, func)
        setattr(f, _ENTRYPOINT_ATTR, self)
        return f

    def is_entrypoint(self, func):
        return getattr(func, _ENTRYPOINT_ATTR, None) is self

    def _wrap(self, func, *args, **kwargs):
        if self.client_mode:
            name = '%s.%s' % (func.__module__, func.__name__)
            if self.channel is None:
                self.start()
            return self.channel.remote_call(name, args, kwargs)
        else:
            return func(*args, **kwargs)

    def start(self, method=Method.ROOTWRAP):
        if self.channel is not None:
            LOG.warning('privsep daemon already running')
            return

        if method is Method.ROOTWRAP:
            channel = daemon.RootwrapClientChannel(context=self)
        elif method is Method.FORK:
            channel = daemon.ForkingClientChannel(context=self)
        else:
            raise ValueError('Unknown method: %s' % method)

        self.channel = channel

    def stop(self):
        if self.channel is not None:
            self.channel.close()
            self.channel = None
