============
oslo.privsep
============

.. image:: https://governance.openstack.org/tc/badges/oslo.privsep.svg

.. Change things from this point on

.. image:: https://img.shields.io/pypi/v/oslo.privsep.svg
    :target: https://pypi.org/project/oslo.privsep/
    :alt: Latest Version

.. image:: https://img.shields.io/pypi/dm/oslo.privsep.svg
    :target: https://pypi.org/project/oslo.privsep/
    :alt: Downloads

OpenStack library for privilege separation

This library helps applications perform actions which require more or
less privileges than they were started with in a safe, easy to code
and easy to use manner. For more information on why this is generally
a good idea please read over the `principle of least privilege`_ and
the `specification`_ which created this library.

* Free software: Apache license
* Documentation: https://docs.openstack.org/oslo.privsep/latest/
* Source: https://opendev.org/openstack/oslo.privsep
* Bugs: https://bugs.launchpad.net/oslo.privsep
* Release Notes: https://docs.openstack.org/releasenotes/oslo.privsep

.. _principle of least privilege:
    https://en.wikipedia.org/wiki/Principle_of_least_privilege
.. _specification:
    https://specs.openstack.org/openstack/oslo-specs/specs/liberty/privsep.html
