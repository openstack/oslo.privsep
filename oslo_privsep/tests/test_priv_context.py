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
import tempfile

import mock
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
class PrivContextTest(testctx.TestContextTestCase):

    @mock.patch.object(priv_context, 'sys')
    def test_init_windows(self, mock_sys):
        mock_sys.platform = 'win32'

        context = priv_context.PrivContext('test', capabilities=[])
        self.assertFalse(context.client_mode)

    @mock.patch.object(priv_context, 'sys')
    def test_set_client_mode(self, mock_sys):
        context = priv_context.PrivContext('test', capabilities=[])
        self.assertTrue(context.client_mode)

        context.set_client_mode(False)
        self.assertFalse(context.client_mode)

        # client_mode should remain to False on win32.
        mock_sys.platform = 'win32'
        self.assertRaises(RuntimeError, context.set_client_mode, True)

    def test_helper_command(self):
        self.privsep_conf.privsep.helper_command = 'foo --bar'
        _, temp_path = tempfile.mkstemp()
        cmd = testctx.context.helper_command(temp_path)
        expected = [
            'foo', '--bar',
            '--privsep_context', testctx.context.pypath,
            '--privsep_sock_path', temp_path,
        ]
        self.assertEqual(expected, cmd)

    def test_helper_command_default(self):
        self.privsep_conf.config_file = ['/bar.conf']
        _, temp_path = tempfile.mkstemp()
        cmd = testctx.context.helper_command(temp_path)
        expected = [
            'sudo', 'privsep-helper',
            '--config-file', '/bar.conf',
            # --config-dir arg should be skipped
            '--privsep_context', testctx.context.pypath,
            '--privsep_sock_path', temp_path,
        ]
        self.assertEqual(expected, cmd)

    def test_helper_command_default_dirtoo(self):
        self.privsep_conf.config_file = ['/bar.conf', '/baz.conf']
        self.privsep_conf.config_dir = ['/foo.d']
        _, temp_path = tempfile.mkstemp()
        cmd = testctx.context.helper_command(temp_path)
        expected = [
            'sudo', 'privsep-helper',
            '--config-file', '/bar.conf',
            '--config-file', '/baz.conf',
            '--config-dir', '/foo.d',
            '--privsep_context', testctx.context.pypath,
            '--privsep_sock_path', temp_path,
        ]
        self.assertEqual(expected, cmd)

    def test_init_known_contexts(self):
        self.assertEqual(testctx.context.helper_command('/sock')[:2],
                         ['sudo', 'privsep-helper'])
        priv_context.init(root_helper=['sudo', 'rootwrap'])
        self.assertEqual(testctx.context.helper_command('/sock')[:3],
                         ['sudo', 'rootwrap', 'privsep-helper'])


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class SeparationTest(testctx.TestContextTestCase):
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
class SerializationTest(testctx.TestContextTestCase):
    def test_basic_functionality(self):
        self.assertEqual(43, add1(42))

    def test_raises_standard(self):
        self.assertRaisesRegexp(
            RuntimeError, "I can't let you do that Dave", fail)

    def test_raises_custom(self):
        exc = self.assertRaises(CustomError, fail, custom=True)
        self.assertEqual(exc.code, 42)
        self.assertEqual(exc.msg, 'omg!')
