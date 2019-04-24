=======
 Usage
=======

oslo.privsep lets you define in your code specific functions that will run
in predefined privilege contexts. This lets you run functions with more (or
less) privileges than the rest of the code. Privsep functions live in a
specific ``privsep`` submodule (for example, ``nova.privsep`` for nova).

Defining a context
==================

Contexts are defined in the ``privsep/__init__.py`` file. For example, this
defines a sys_admin_pctxt with ``CAP_CHOWN``, ``CAP_DAC_OVERRIDE``,
``CAP_DAC_READ_SEARCH``, ``CAP_FOWNER``, ``CAP_NET_ADMIN``, and
``CAP_SYS_ADMIN`` rights (equivalent to ``sudo`` rights)::

  from oslo_privsep import capabilities
  from oslo_privsep import priv_context

  sys_admin_pctxt = priv_context.PrivContext(
      'nova',
      cfg_section='nova_sys_admin',
      pypath=__name__ + '.sys_admin_pctxt',
      capabilities=[capabilities.CAP_CHOWN,
                    capabilities.CAP_DAC_OVERRIDE,
                    capabilities.CAP_DAC_READ_SEARCH,
                    capabilities.CAP_FOWNER,
                    capabilities.CAP_NET_ADMIN,
                    capabilities.CAP_SYS_ADMIN],
  )

Defining a privileged function
==============================

Functions are defined in files under the ``privsep/`` subdirectory, for
example in a ``privsep/motd.py`` file for functions touching the MOTD file.
They make use of a decorator pointing to the context we defined above::

  import nova.privsep

  @nova.privsep.sys_admin_pctxt.entrypoint
  def update_motd(message):
      with open('/etc/motd', 'w') as f:
          f.write(message)

Privileged functions must be as simple, specialized and narrow as possible,
so as to prevent further escalation. In this example, ``update_motd(message)``
is narrow: it only allows the service to overwrite the MOTD file. If a more
generic ``update_file(filename, content)`` was created, it could be used to
overwrite any file in the filesystem, allowing easy escalation to root
rights. That would defeat the whole purpose of oslo.privsep.


Using a privileged function
===========================

To use the privileged function in the regular code, you can just call it::

  import nova.privsep.motd
  ...

  nova.privsep.motd.update_motd('This node is currently idle')

It is better to import the complete path (``import nova.privsep.motd``) rather
than the motd name (``from nova.privsep import motd``) so that it is easier to
spot that the function runs in a different privileged context.

For more details, you can read the following blog post:

* `How to make a privileged call with oslo privsep`_

.. _How to make a privileged call with oslo privsep: https://www.madebymikal.com/how-to-make-a-privileged-call-with-oslo-privsep/


Converting from rootwrap to privsep
===================================

oslo.rootwrap is a precursor of oslo.privsep to allow code to run commands
under sudo if they match a predefined filter. For example, you could define
a filter that would allow you to run chmod as root using the following
filter::

  chmod: CommandFilter, chmod, root

Beyond the bad performance of calling full commands in order to accomplish
simple tasks, rootwrap also led to bad security: it was difficult to filter
commands in a way that would not easily allow privilege escalation.

Replacing rootwrap filters with privsep functions is easy. The chmod filter
above can be replaced with a function that calls ``os.chmod()``. However a
straight 1:1 filter:function replacement generally results in functions that
are still too broad for good security. It is better to replace each chmod
rootwrap *call* with a narrow privsep function that will limit it to specific
files.

Sometimes it is necessary to refactor the calling code: the rootwrap design
discouraged the creation of new filters and therefore often resulted in the
creation of overly-broad calling functions.

As an example, this `patch series`_ is work-in-progress to transition Nova
from rootwrap to privsep.

For more details, you can read the following blog post:

* `Adding oslo privsep to a new project, a worked example`_

.. _patch series: https://review.openstack.org/#/q/project:openstack/nova+branch:master+topic:my-own-personal-alternative-universe

.. _Adding oslo privsep to a new project, a worked example: https://www.madebymikal.com/adding-oslo-privsep-to-a-new-project-a-worked-example/
