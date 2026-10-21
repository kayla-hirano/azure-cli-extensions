Microsoft Azure CLI 'ace' Extension
==========================================

This package is for the 'ace' extension.
i.e. 'az ace'

Commands
--------

* ``az ace setup``: Install and validate all dependencies.
* ``az ace create``: Pack the workload into the initrd and create the enclave IGVM file.
* ``az ace launch``: Use OpenVMM RPC to create and launch the enclave VM from the IGVM file.
* ``az ace destroy``: Use OpenVMM RPC to destroy the enclave.

These commands are placeholders. Arguments and backend functionality will be
added later. Each command currently raises a "not implemented yet" error and
does not install dependencies, create artifacts, or contact OpenVMM.

Use ``az ace --help`` or ``az ace <command> --help`` to view the command structure.