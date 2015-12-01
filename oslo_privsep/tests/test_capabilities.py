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

import mock

from oslotest import base

from oslo_privsep import capabilities


class TestCapabilities(base.BaseTestCase):

    @mock.patch('oslo_privsep.capabilities._prctl')
    def test_set_keepcaps_error(self, mock_prctl):
        mock_prctl.return_value = -1
        self.assertRaises(OSError, capabilities.set_keepcaps, True)

    @mock.patch('oslo_privsep.capabilities._prctl')
    def test_set_keepcaps(self, mock_prctl):
        mock_prctl.return_value = 0
        capabilities.set_keepcaps(True)

        # Disappointingly, ffi.cast(type, 1) != ffi.cast(type, 1)
        # so can't just use assert_called_once_with :-(
        self.assertEqual(1, mock_prctl.call_count)
        self.assertItemsEqual(
            [8, 1],  # [PR_SET_KEEPCAPS, true]
            [int(x) for x in mock_prctl.call_args[0]])

    @mock.patch('oslo_privsep.capabilities._capset')
    def test_drop_all_caps_except_error(self, mock_capset):
        mock_capset.return_value = -1
        self.assertRaises(
            OSError, capabilities.drop_all_caps_except, [0], [0], [0])

    @mock.patch('oslo_privsep.capabilities._capset')
    def test_drop_all_caps_except(self, mock_capset):
        mock_capset.return_value = 0

        # Somewhat arbitrary bit patterns to exercise _caps_to_mask
        capabilities.drop_all_caps_except(
            (17, 24, 49), (8, 10, 35, 56), (24, 31, 40))

        self.assertEqual(1, mock_capset.call_count)
        hdr, data = mock_capset.call_args[0]
        self.assertEqual(0x20071026,  # _LINUX_CAPABILITY_VERSION_2
                         hdr.version)
        self.assertEqual(0x01020000, data[0].effective)
        self.assertEqual(0x00020000, data[1].effective)
        self.assertEqual(0x00000500, data[0].permitted)
        self.assertEqual(0x01000008, data[1].permitted)
        self.assertEqual(0x81000000, data[0].inheritable)
        self.assertEqual(0x00000100, data[1].inheritable)

    @mock.patch('oslo_privsep.capabilities._capget')
    def test_get_caps_error(self, mock_capget):
        mock_capget.return_value = -1
        self.assertRaises(OSError, capabilities.get_caps)

    @mock.patch('oslo_privsep.capabilities._capget')
    def test_get_caps(self, mock_capget):
        def impl(hdr, data):
            # Somewhat arbitrary bit patterns to exercise _mask_to_caps
            data[0].effective = 0x01020000
            data[1].effective = 0x00020000
            data[0].permitted = 0x00000500
            data[1].permitted = 0x01000008
            data[0].inheritable = 0x81000000
            data[1].inheritable = 0x00000100
            return 0
        mock_capget.side_effect = impl

        self.assertItemsEqual(
            ([17, 24, 49],
             [8, 10, 35, 56],
             [24, 31, 40]),
            capabilities.get_caps())
