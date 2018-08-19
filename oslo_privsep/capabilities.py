# Copyright 2015 Rackspace Hosting
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

import enum
import os
import platform
import sys

import cffi


class Capabilities(enum.IntEnum):
    # Generated with:
    # awk '/^#define CAP_[A-Z_]+[ \t]+[0-9]+/ {print $2,"=",$3}' \
    #        include/uapi/linux/capability.h
    # From the 4.11.11 kernel and the kernel git SHA:235b84fc862
    # Will need to be refreshed as new capabilites are added to the kernel
    CAP_CHOWN = 0
    CAP_DAC_OVERRIDE = 1
    CAP_DAC_READ_SEARCH = 2
    CAP_FOWNER = 3
    CAP_FSETID = 4
    CAP_KILL = 5
    CAP_SETGID = 6
    CAP_SETUID = 7
    CAP_SETPCAP = 8
    CAP_LINUX_IMMUTABLE = 9
    CAP_NET_BIND_SERVICE = 10
    CAP_NET_BROADCAST = 11
    CAP_NET_ADMIN = 12
    CAP_NET_RAW = 13
    CAP_IPC_LOCK = 14
    CAP_IPC_OWNER = 15
    CAP_SYS_MODULE = 16
    CAP_SYS_RAWIO = 17
    CAP_SYS_CHROOT = 18
    CAP_SYS_PTRACE = 19
    CAP_SYS_PACCT = 20
    CAP_SYS_ADMIN = 21
    CAP_SYS_BOOT = 22
    CAP_SYS_NICE = 23
    CAP_SYS_RESOURCE = 24
    CAP_SYS_TIME = 25
    CAP_SYS_TTY_CONFIG = 26
    CAP_MKNOD = 27
    CAP_LEASE = 28
    CAP_AUDIT_WRITE = 29
    CAP_AUDIT_CONTROL = 30
    CAP_SETFCAP = 31
    CAP_MAC_OVERRIDE = 32
    CAP_MAC_ADMIN = 33
    CAP_SYSLOG = 34
    CAP_WAKE_ALARM = 35
    CAP_BLOCK_SUSPEND = 36
    CAP_AUDIT_READ = 37


CAPS_BYNAME = {}
CAPS_BYVALUE = {}
module = sys.modules[__name__]
# Convenience dicts for human readable values
# module attributes for backwards compat/convenience
for c in Capabilities:
    CAPS_BYNAME[c.name] = c.value
    CAPS_BYVALUE[c.value] = c.name
    setattr(module, c.name, c.value)

CDEF = '''
/* Edited highlights from `echo '#include <sys/capability.h>' | gcc -E -` */

#define _LINUX_CAPABILITY_VERSION_2  0x20071026
#define _LINUX_CAPABILITY_U32S_2     2

typedef unsigned int __u32;

typedef struct __user_cap_header_struct {
        __u32 version;
        int pid;
} *cap_user_header_t;

typedef struct __user_cap_data_struct {
        __u32 effective;
        __u32 permitted;
        __u32 inheritable;
} *cap_user_data_t;

int capset(cap_user_header_t header, const cap_user_data_t data);
int capget(cap_user_header_t header, cap_user_data_t data);


/* Edited highlights from `echo '#include <sys/prctl.h>' | gcc -E -` */

#define PR_GET_KEEPCAPS   7
#define PR_SET_KEEPCAPS   8

int prctl (int __option, ...);
'''

ffi = cffi.FFI()
ffi.cdef(CDEF)


if platform.system() == 'Linux':
    # mock.patching crt.* directly seems to upset cffi.  Use an
    # indirection point here for easier testing.
    crt = ffi.dlopen(None)
    _prctl = crt.prctl
    _capget = crt.capget
    _capset = crt.capset
else:
    _prctl = None
    _capget = None
    _capset = None


def set_keepcaps(enable):
    """Set/unset thread's "keep capabilities" flag - see prctl(2)"""
    ret = _prctl(crt.PR_SET_KEEPCAPS,
                 ffi.cast('unsigned long', bool(enable)))
    if ret != 0:
        errno = ffi.errno
        raise OSError(errno, os.strerror(errno))


def drop_all_caps_except(effective, permitted, inheritable):
    """Set (effective, permitted, inheritable) to provided list of caps"""
    eff = _caps_to_mask(effective)
    prm = _caps_to_mask(permitted)
    inh = _caps_to_mask(inheritable)

    header = ffi.new('cap_user_header_t',
                     {'version': crt._LINUX_CAPABILITY_VERSION_2,
                      'pid': 0})
    data = ffi.new('struct __user_cap_data_struct[2]')
    data[0].effective = eff & 0xffffffff
    data[1].effective = eff >> 32
    data[0].permitted = prm & 0xffffffff
    data[1].permitted = prm >> 32
    data[0].inheritable = inh & 0xffffffff
    data[1].inheritable = inh >> 32

    ret = _capset(header, data)
    if ret != 0:
        errno = ffi.errno
        raise OSError(errno, os.strerror(errno))


def _mask_to_caps(mask):
    """Convert bitmask to list of set bit offsets"""
    return [i for i in range(64) if (1 << i) & mask]


def _caps_to_mask(caps):
    """Convert list of bit offsets to bitmask"""
    mask = 0
    for cap in caps:
        mask |= 1 << cap
    return mask


def get_caps():
    """Return (effective, permitted, inheritable) as lists of caps"""
    header = ffi.new('cap_user_header_t',
                     {'version': crt._LINUX_CAPABILITY_VERSION_2,
                      'pid': 0})
    data = ffi.new('struct __user_cap_data_struct[2]')
    ret = _capget(header, data)
    if ret != 0:
        errno = ffi.errno
        raise OSError(errno, os.strerror(errno))

    return (
        _mask_to_caps(data[0].effective |
                      (data[1].effective << 32)),
        _mask_to_caps(data[0].permitted |
                      (data[1].permitted << 32)),
        _mask_to_caps(data[0].inheritable |
                      (data[1].inheritable << 32)),
    )
