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

import copy
import eventlet
import fixtures
import functools
import logging as pylogging
import platform
import sys
import time
from unittest import mock

from oslo_log import formatters
from oslo_log import log as logging
from oslotest import base
import six
import testtools

from oslo_privsep import capabilities
from oslo_privsep import comm
from oslo_privsep import daemon
from oslo_privsep.tests import testctx


LOG = logging.getLogger(__name__)


def undecorated():
    pass


class TestException(Exception):
    pass


def get_fake_context(conf_attrs=None, **context_attrs):
    conf_attrs = conf_attrs or {}
    context = mock.NonCallableMock()
    context.conf.user = 42
    context.conf.group = 84
    context.conf.thread_pool_size = 10
    context.conf.capabilities = [
        capabilities.CAP_SYS_ADMIN, capabilities.CAP_NET_ADMIN]
    context.conf.logger_name = 'oslo_privsep.daemon'
    vars(context).update(context_attrs)
    vars(context.conf).update(conf_attrs)
    return context


@testctx.context.entrypoint
def logme(level, msg, exc_info=False):
    # We want to make sure we log everything from the priv side for
    # the purposes of this test, so force loglevel.
    LOG.logger.setLevel(logging.DEBUG)
    if exc_info:
        try:
            raise TestException('with arg')
        except TestException:
            LOG.log(level, msg, exc_info=True)
    else:
        LOG.log(level, msg)


class LogRecorder(pylogging.Formatter):
    def __init__(self, logs, *args, **kwargs):
        if sys.version_info >= (3, 8):
            kwargs['validate'] = False
        super(LogRecorder, self).__init__(*args, **kwargs)
        self.logs = logs

    def format(self, record):
        self.logs.append(copy.deepcopy(record))
        return super(LogRecorder, self).format(record)


@testtools.skipIf(platform.system() != 'Linux',
                  'works only on Linux platform.')
class LogTest(testctx.TestContextTestCase):
    def setUp(self):
        super(LogTest, self).setUp()

    def test_priv_loglevel(self):
        logger = self.useFixture(fixtures.FakeLogger(
            level=logging.INFO))

        # These write to the log on the priv side
        logme(logging.DEBUG, u'test@DEBUG')
        logme(logging.WARN, u'test@WARN')

        time.sleep(0.1)  # Hack to give logging thread a chance to run

        # logger.output is the resulting log on the unpriv side.
        # This should have been filtered based on (unpriv) loglevel.
        self.assertNotIn(u'test@DEBUG', logger.output)
        self.assertIn(u'test@WARN', logger.output)

    def test_record_data(self):
        logs = []

        self.useFixture(fixtures.FakeLogger(
            level=logging.INFO, format='dummy',
            # fixtures.FakeLogger accepts only a formatter
            # class/function, not an instance :(
            formatter=functools.partial(LogRecorder, logs)))

        logme(logging.WARN, u'test with exc', exc_info=True)

        time.sleep(0.1)  # Hack to give logging thread a chance to run

        self.assertEqual(1, len(logs))

        record = logs[0]
        self.assertIn(u'test with exc', record.getMessage())
        self.assertIsNone(record.exc_info)
        self.assertIn(u'TestException: with arg', record.exc_text)
        self.assertEqual('PrivContext(cfg_section=privsep)',
                         record.processName)
        self.assertIn(u'test_daemon.py', record.exc_text)
        self.assertEqual(logging.WARN, record.levelno)
        self.assertEqual('logme', record.funcName)

    def test_format_record(self):
        logs = []

        self.useFixture(fixtures.FakeLogger(
            level=logging.INFO, format='dummy',
            # fixtures.FakeLogger accepts only a formatter
            # class/function, not an instance :(
            formatter=functools.partial(LogRecorder, logs)))

        logme(logging.WARN, u'test with exc', exc_info=True)

        time.sleep(0.1)  # Hack to give logging thread a chance to run

        self.assertEqual(1, len(logs))

        record = logs[0]
        # Verify the log record can be formatted by ContextFormatter
        fake_config = mock.Mock(
            logging_default_format_string="NOCTXT: %(message)s")
        formatter = formatters.ContextFormatter(config=fake_config)
        formatter.format(record)


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
        context = get_fake_context()

        d = daemon.Daemon(channel, context)
        d._drop_privs()

        mock_setuid.assert_called_once_with(42)
        mock_setgid.assert_called_once_with(84)
        mock_setgroups.assert_called_once_with([])

        self.assertCountEqual(
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
        self.assertRaisesRegex(
            NameError, 'undecorated not exported',
            testctx.context._wrap, undecorated)


class ClientChannelTestCase(base.BaseTestCase):

    DICT = {
        'string_1': ('tuple_1', six.b('tuple_2')),
        six.b('byte_1'): ['list_1', 'list_2'],
    }

    EXPECTED = {
        'string_1': ('tuple_1', six.b('tuple_2')),
        'byte_1': ['list_1', 'list_2'],
    }

    def setUp(self):
        super(ClientChannelTestCase, self).setUp()
        context = get_fake_context()
        with mock.patch.object(comm.ClientChannel, '__init__'), \
                mock.patch.object(daemon._ClientChannel, 'exchange_ping'):
            self.client_channel = daemon._ClientChannel(mock.ANY, context)

    @mock.patch.object(daemon.LOG.logger, 'handle')
    def test_out_of_band_log_message(self, handle_mock):
        message = [comm.Message.LOG, self.DICT]
        self.assertEqual(self.client_channel.log, daemon.LOG)
        with mock.patch.object(pylogging, 'makeLogRecord') as mock_make_log, \
                mock.patch.object(daemon.LOG, 'isEnabledFor',
                                  return_value=True) as mock_enabled:
            self.client_channel.out_of_band(message)
            mock_make_log.assert_called_once_with(self.EXPECTED)
            handle_mock.assert_called_once_with(mock_make_log.return_value)
            mock_enabled.assert_called_once_with(
                mock_make_log.return_value.levelno)

    def test_out_of_band_not_log_message(self):
        with mock.patch.object(daemon.LOG, 'warning') as mock_warning:
            self.client_channel.out_of_band([comm.Message.PING])
            mock_warning.assert_called_once()

    @mock.patch.object(daemon.logging, 'getLogger')
    @mock.patch.object(pylogging, 'makeLogRecord')
    def test_out_of_band_log_message_context_logger(self, make_log_mock,
                                                    get_logger_mock):
        logger_name = 'os_brick.privileged'
        context = get_fake_context(conf_attrs={'logger_name': logger_name})
        with mock.patch.object(comm.ClientChannel, '__init__'), \
                mock.patch.object(daemon._ClientChannel, 'exchange_ping'):
            channel = daemon._ClientChannel(mock.ANY, context)

        get_logger_mock.assert_called_once_with(logger_name)
        self.assertEqual(get_logger_mock.return_value, channel.log)

        message = [comm.Message.LOG, self.DICT]
        channel.out_of_band(message)

        make_log_mock.assert_called_once_with(self.EXPECTED)
        channel.log.isEnabledFor.assert_called_once_with(
            make_log_mock.return_value.levelno)
        channel.log.logger.handle.assert_called_once_with(
            make_log_mock.return_value)


class UnMonkeyPatch(base.BaseTestCase):

    def test_un_monkey_patch(self):
        self.assertFalse(any(
            eventlet.patcher.is_monkey_patched(eventlet_mod_name)
            for eventlet_mod_name in daemon.EVENTLET_MODULES))

        eventlet.monkey_patch()
        self.assertTrue(any(
            eventlet.patcher.is_monkey_patched(eventlet_mod_name)
            for eventlet_mod_name in daemon.EVENTLET_MODULES))

        daemon.un_monkey_patch()
        for eventlet_mod_name, func_modules in daemon.EVENTLET_LIBRARIES:
            if not eventlet.patcher.is_monkey_patched(eventlet_mod_name):
                continue

            for name, green_mod in func_modules():
                orig_mod = eventlet.patcher.original(name)
                patched_mod = sys.modules.get(name)
                for attr_name in green_mod.__patched__:
                    un_monkey_patched_attr = getattr(patched_mod, attr_name,
                                                     None)
                    original_attr = getattr(orig_mod, attr_name, None)
                    self.assertEqual(un_monkey_patched_attr, original_attr)
