.. :changelog:

Release History
===============

0.1.0
++++++
* Initial release.
* Rename the extension and command group from ``ace`` to ``enclave``.
* Switch the default instance root to ``/var/lib/enclave/instances`` and
  host lock to ``/run/enclave/enclave.lock``. Stop all old runtimes and retire
  old installations before upgrading; state is not automatically migrated.