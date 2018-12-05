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

import six

from oslotest import base

from oslo_privsep import comm


class BufSock(object):
    def __init__(self):
        self.readpos = 0
        self.buf = six.BytesIO()

    def recv(self, bufsize):
        if self.buf.closed:
            return b''
        self.buf.seek(self.readpos, 0)
        data = self.buf.read(bufsize)
        self.readpos += len(data)
        return data

    def sendall(self, data):
        self.buf.seek(0, 2)
        self.buf.write(data)

    def shutdown(self, _flag):
        self.buf.close()


class TestSerialization(base.BaseTestCase):
    def setUp(self):
        super(TestSerialization, self).setUp()

        sock = BufSock()

        self.input = comm.Serializer(sock)
        self.output = iter(comm.Deserializer(sock))

    def send(self, data):
        self.input.send(data)
        return next(self.output)

    def assertSendable(self, value):
        self.assertEqual(value, self.send(value))

    def test_none(self):
        self.assertSendable(None)

    def test_bool(self):
        self.assertSendable(True)
        self.assertSendable(False)

    def test_int(self):
        self.assertSendable(42)
        self.assertSendable(-84)

    def test_bytes(self):
        data = b'\x00\x01\x02\xfd\xfe\xff'
        self.assertSendable(data)

    def test_unicode(self):
        data = u'\u4e09\u9df9\udc82'
        self.assertSendable(data)

    def test_tuple(self):
        self.assertSendable((1, 'foo'))

    def test_list(self):
        # NB! currently lists get converted to tuples by serialization.
        self.assertEqual((1, 'foo'), self.send([1, 'foo']))

    def test_dict(self):
        self.assertSendable(
            {
                'a': 'b',
                1: 2,
                None: None,
                (1, 2): (3, 4),
            }
        )

    def test_badobj(self):
        class UnknownClass(object):
            pass

        obj = UnknownClass()
        self.assertRaises(TypeError, self.send, obj)

    def test_eof(self):
        self.input.close()
        self.assertRaises(StopIteration, next, self.output)
