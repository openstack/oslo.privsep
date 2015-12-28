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


import logging
import os
import pipes
import platform
import sys

import testtools

from oslo_privsep import daemon
from oslo_privsep import priv_context
from oslo_privsep.tests import testctx

LOG = logging.getLogger(__name__)


@testctx.context.entrypoint
def priv_getpid():
    return os.getpid()


@testctx.context.entrypoint
def add1(arg):
    return arg + 1


class CustomError(Exception):
    def __init__(self, code, msg):
        super(CustomError, self).__init__(code, msg)
        self.code = code
        self.msg = msg

    def __str__(self):
        return 'Code %s: %s' % (self.code, self.msg)


@testctx.context.entrypoint
def fail(custom=False):
    if custom:
        raise CustomError(42, 'omg!')
    else:
        raise RuntimeError("I can't let you do that Dave")


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class TestSeparation(testctx.TestContextTestCase):
    def test_getpid(self):
        # Verify that priv_getpid() was executed in another process.
        priv_pid = priv_getpid()
        self.assertNotMyPid(priv_pid)

    def test_client_mode(self):
        self.assertNotMyPid(priv_getpid())

        self.addCleanup(testctx.context.set_client_mode, True)

        testctx.context.set_client_mode(False)
        # priv_getpid() should now run locally (and return our pid)
        self.assertEqual(os.getpid(), priv_getpid())

        testctx.context.set_client_mode(True)
        # priv_getpid() should now run remotely again
        self.assertNotMyPid(priv_getpid())


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class RootwrapTest(testctx.TestContextTestCase):
    def setUp(self):
        super(RootwrapTest, self).setUp()
        testctx.context.stop()

        # Generate a command that will run daemon.helper_main without
        # requiring it to be properly installed.
        cmd = [
            'env',
            'PYTHON_PATH=%s' % os.path.pathsep.join(sys.path),
            sys.executable, daemon.__file__,
        ]
        if LOG.isEnabledFor(logging.DEBUG):
            cmd.append('--debug')

        self.privsep_conf.set_override(
            'helper_command', ' '.join(map(pipes.quote, cmd)),
            group=testctx.context.cfg_section)

        testctx.context.start(method=priv_context.Method.ROOTWRAP)

    def test_getpid(self):
        # Verify that priv_getpid() was executed in another process.
        priv_pid = priv_getpid()
        self.assertNotMyPid(priv_pid)


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class TestSerialization(testctx.TestContextTestCase):
    def test_basic_functionality(self):
        self.assertEqual(43, add1(42))

    def test_raises_standard(self):
        self.assertRaisesRegexp(
            RuntimeError, "I can't let you do that Dave", fail)

    def test_raises_custom(self):
        exc = self.assertRaises(CustomError, fail, custom=True)
        self.assertEqual(exc.code, 42)
        self.assertEqual(exc.msg, 'omg!')
