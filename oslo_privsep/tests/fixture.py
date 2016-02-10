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


import fixtures
import logging
import os
import sys

from oslo_config import fixture as cfg_fixture

from oslo_privsep import priv_context

LOG = logging.getLogger(__name__)


class UnprivilegedPrivsepFixture(fixtures.Fixture):
    def __init__(self, context):
        self.context = context

    def setUp(self):
        super(UnprivilegedPrivsepFixture, self).setUp()

        self.conf = self.useFixture(cfg_fixture.Config()).conf
        self.conf.set_override('capabilities', [],
                               group=self.context.cfg_section)
        for k in ('user', 'group'):
            self.conf.set_override(
                k, None, group=self.context.cfg_section)

        orig_pid = os.getpid()
        try:
            self.context.start(method=priv_context.Method.FORK)
        except Exception as e:
            # py3 unittest/testtools/something catches fatal
            # exceptions from child processes and tries to treat them
            # like regular non-fatal test failures.  Here we attempt
            # to undo that.
            if os.getpid() == orig_pid:
                raise
            LOG.exception(e)
            sys.exit(1)

        self.addCleanup(self.context.stop)
