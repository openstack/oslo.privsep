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

from oslo_config import cfg
from oslo_config import types

from oslo_privsep import capabilities
from oslo_privsep import daemon
from oslo_privsep._i18n import _, _LW


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


@enum.unique
class Method(enum.Enum):
    FORK = 1
    ROOTWRAP = 2


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
        self.client_mode = True
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

    def set_client_mode(self, enabled):
        self.client_mode = enabled

    def entrypoint(self, func):
        """This is intended to be used as a decorator."""

        assert func.__module__.startswith(self.prefix), (
            '%r entrypoints must be below "%s"' % (self, self.prefix))

        # Right now, we only track a single context in
        # _ENTRYPOINT_ATTR.  This could easily be expanded into a set,
        # but that will increase the memory overhead.  Revisit if/when
        # someone has a need to associate the same entrypoint with
        # multiple contexts.
        assert getattr(func, _ENTRYPOINT_ATTR, None) is None, (
            '%r is already associated with another PrivContext' % func)

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
            LOG.warning(_LW('privsep daemon already running'))
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
