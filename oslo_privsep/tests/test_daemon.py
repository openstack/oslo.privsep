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
import mock
import platform
import time

from oslo_log import log as logging
from oslotest import base
import testtools

from oslo_privsep import capabilities
from oslo_privsep import daemon
from oslo_privsep.tests import testctx


LOG = logging.getLogger(__name__)


def undecorated():
    pass


@testctx.context.entrypoint
def logme(level, msg):
    # We want to make sure we log everything from the priv side for
    # the purposes of this test, so force loglevel.
    LOG.logger.setLevel(logging.DEBUG)
    LOG.log(level, msg)


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class LogTest(testctx.TestContextTestCase):
    def setUp(self):
        super(LogTest, self).setUp()
        self.logger = self.useFixture(fixtures.FakeLogger(
            name=None, level=logging.INFO))

    def test_priv_log(self):
        # These write to the log on the priv side
        logme(logging.DEBUG, u'test@DEBUG')
        logme(logging.WARN, u'test@WARN')

        time.sleep(0.1)  # Hack to give logging thread a chance to run

        # self.logger.output is the resulting log on the unpriv side
        self.assertNotIn(u'test@DEBUG', self.logger.output)
        self.assertIn(u'test@WARN', self.logger.output)


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class DaemonTest(base.BaseTestCase):

    @mock.patch('os.setuid')
    @mock.patch('os.setgid')
    @mock.patch('os.setgroups')
    @mock.patch('oslo_privsep.capabilities.set_keepcaps')
    @mock.patch('oslo_privsep.capabilities.drop_all_caps_except')
    def test_drop_privs(self, mock_dropcaps, mock_keepcaps,
                        mock_setgroups, mock_setgid, mock_setuid):
        channel = mock.NonCallableMock()
        context = mock.NonCallableMock()
        context.conf.user = 42
        context.conf.group = 84
        context.conf.capabilities = [
            capabilities.CAP_SYS_ADMIN, capabilities.CAP_NET_ADMIN]

        d = daemon.Daemon(channel, context)
        d._drop_privs()

        mock_setuid.assert_called_once_with(42)
        mock_setgid.assert_called_once_with(84)
        mock_setgroups.assert_called_once_with([])

        self.assertItemsEqual(
            [mock.call(True), mock.call(False)],
            mock_keepcaps.mock_calls)

        mock_dropcaps.assert_called_once_with(
            set((capabilities.CAP_SYS_ADMIN, capabilities.CAP_NET_ADMIN)),
            set((capabilities.CAP_SYS_ADMIN, capabilities.CAP_NET_ADMIN)),
            [])


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class WithContextTest(testctx.TestContextTestCase):

    def test_unexported(self):
        self.assertRaisesRegexp(
            NameError, 'undecorated not exported',
            testctx.context._wrap, undecorated)
