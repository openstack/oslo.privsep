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

import os

from oslotest import base

from oslo_privsep import priv_context
import oslo_privsep.tests
from oslo_privsep.tests import fixture


context = priv_context.PrivContext(
    # This context allows entrypoints anywhere below oslo_privsep.tests.
    oslo_privsep.tests.__name__,
    pypath=__name__ + '.context',
    # This is one of the rare cases where we actually want zero powers:
    capabilities=[],
)


class TestContextTestCase(base.BaseTestCase):
    def setUp(self):
        super(TestContextTestCase, self).setUp()
        privsep_fixture = self.useFixture(
            fixture.UnprivilegedPrivsepFixture(context))
        self.privsep_conf = privsep_fixture.conf

    def assertNotMyPid(self, pid):
        # Verify that `pid` is some positive integer, that isn't our pid
        self.assertIsInstance(pid, int)
        self.assertTrue(pid > 0)
        self.assertNotEqual(os.getpid(), pid)
