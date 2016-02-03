============
oslo.privsep
============

.. image:: https://img.shields.io/pypi/v/oslo.privsep.svg
    :target: https://pypi.python.org/pypi/oslo.privsep/
    :alt: Latest Version

.. image:: https://img.shields.io/pypi/dm/oslo.privsep.svg
    :target: https://pypi.python.org/pypi/oslo.privsep/
    :alt: Downloads

OpenStack library for privilege separation

This library helps applications perform actions which require more or
less privileges than they were started with in a safe, easy to code
and easy to use manner. For more information on why this is generally
a good idea please read over the `principle of least privilege`_ and
the `specification`_ which created this library.

* Free software: Apache license
* Documentation: http://docs.openstack.org/developer/oslo.privsep
* Source: http://git.openstack.org/cgit/openstack/oslo.privsep
* Bugs: http://bugs.launchpad.net/oslo.privsep

.. _principle of least privilege: https://en.wikipedia.org/wiki/\
                                  Principle_of_least_privilege
.. _specification: https://specs.openstack.org/openstack/\
                   oslo-specs/specs/liberty/privsep.html
