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
import time

from oslo_log import log as logging

from oslo_privsep.tests import testctx


LOG = logging.getLogger(__name__)


def undecorated():
    pass


@testctx.context.entrypoint
def logme(level, msg):
    LOG.log(level, '%s', msg)


class LogTest(testctx.TestContextTestCase):
    def setUp(self):
        super(LogTest, self).setUp()
        self.logger = self.useFixture(fixtures.FakeLogger(
            name=None, level=logging.INFO))

    def test_priv_log(self):
        logme(logging.DEBUG, 'test@DEBUG')
        logme(logging.WARN, 'test@WARN')

        time.sleep(0.1)  # Hack to give logging thread a chance to run

        # TODO(gus): Currently severity information is lost and
        # everything is logged as INFO.  Fixing this probably requires
        # writing structured messages to the logging socket.
        #
        # self.assertNotIn('test@DEBUG', self.logger.output)

        self.assertIn('test@WARN', self.logger.output)


class TestDaemon(testctx.TestContextTestCase):

    def test_unexported(self):
        self.assertRaisesRegexp(
            NameError, 'undecorated not exported',
            testctx.context._wrap, undecorated)
