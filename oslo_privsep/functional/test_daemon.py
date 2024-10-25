# Copyright 2019 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import os
import time
import unittest

from oslo_config import fixture as config_fixture
from oslotest import base

from oslo_privsep import comm
from oslo_privsep import priv_context


test_context = priv_context.PrivContext(
    __name__,
    cfg_section='privsep',
    pypath=__name__ + '.test_context',
    capabilities=[],
)

test_context_with_timeout = priv_context.PrivContext(
    __name__,
    cfg_section='privsep',
    pypath=__name__ + '.test_context_with_timeout',
    capabilities=[],
    timeout=0.03
)


@test_context.entrypoint
def sleep():
    # We don't want the daemon to be able to handle these calls too fast.
    time.sleep(.001)


@test_context.entrypoint_with_timeout(0.03)
def sleep_with_timeout(long_timeout=0.04):
    time.sleep(long_timeout)
    return 42


@test_context_with_timeout.entrypoint
def sleep_with_t_context(long_timeout=0.04):
    time.sleep(long_timeout)
    return 42


@test_context.entrypoint
def one():
    return 1


@test_context.entrypoint
def logs():
    logging.warning('foo')


class TestDaemon(base.BaseTestCase):
    def setUp(self):
        super().setUp()
        venv_path = os.environ['VIRTUAL_ENV']
        self.cfg_fixture = self.useFixture(config_fixture.Config())
        self.cfg_fixture.config(
            group='privsep',
            helper_command='sudo -E %s/bin/privsep-helper' % venv_path)
        priv_context.init()

    def test_concurrency(self):
        # Throw a large number of simultaneous requests at the daemon to make
        # sure it can can handle them.
        for i in range(1000):
            sleep()
        # Make sure the daemon is still working
        self.assertEqual(1, one())

    def test_entrypoint_with_timeout(self):
        thread_pool_size = self.cfg_fixture.conf.privsep.thread_pool_size
        for _ in range(thread_pool_size + 1):
            self.assertRaises(comm.PrivsepTimeout, sleep_with_timeout)

    def test_entrypoint_with_timeout_pass(self):
        thread_pool_size = self.cfg_fixture.conf.privsep.thread_pool_size
        for _ in range(thread_pool_size + 1):
            res = sleep_with_timeout(0.01)
            self.assertEqual(42, res)

    def test_context_with_timeout(self):
        thread_pool_size = self.cfg_fixture.conf.privsep.thread_pool_size
        for _ in range(thread_pool_size + 1):
            self.assertRaises(comm.PrivsepTimeout, sleep_with_t_context)

    def test_context_with_timeout_pass(self):
        thread_pool_size = self.cfg_fixture.conf.privsep.thread_pool_size
        for _ in range(thread_pool_size + 1):
            res = sleep_with_t_context(0.01)
            self.assertEqual(42, res)

    def test_logging(self):
        logs()
        self.assertIn('foo', self.log_fixture.logger.output)


if __name__ == '__main__':
    unittest.main()
