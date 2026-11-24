Command-line reference
======================

.. code-block:: console

   $ emanafa --help

Session
-------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Option
     - Description
   * - ``-a, --app_package <pkg>``
     - App to measure. Starts the method trace (``am``).
   * - ``-s, --time_in_secs <n>``
     - Measure for *n* seconds.
   * - ``-cmd, --command "<cmd>"``
     - Run this command on the host, and stop when it is complete.
   * - ``-pm, --profile-mode``
     - ``energy`` (default), ``memory``, ``both`` or ``legacy``.
   * - ``--force-legacy``
     - Use the legacy model.

Methods and diagram
-------------------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Option
     - Description
   * - ``--trace-methods``
     - ``am``, ``hunter`` or ``none``.
   * - ``-ht, --hunter``
     - Same as ``--trace-methods hunter``.
   * - ``--sunburst [FILE]``
     - Write the sunburst diagram. Needs ``-a`` and plotly.

Sampling
--------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Option
     - Description
   * - ``--meminfo-period-ms <n>``
     - Period of memory polling. Default 50.
   * - ``--battery-poll-ms <n>``
     - Period of power rail polling. Default 250.

Device data
-----------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Option
     - Description
   * - ``-p, --profile <file>``
     - ``power_profile.xml`` for the legacy model.
   * - ``-t, --timezone <tz>``
     - Timezone of the device, if detection fails. IANA name (``Europe/Lisbon``) or UTC
       offset (``+0100``).

Output
------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Option
     - Description
   * - ``-o, --output_file <file>``
     - Result file.
   * - ``-of, --output-format``
     - ``json`` (default) or ``csv``.

Recorded files
--------------

.. list-table::
   :header-rows: 1
   :widths: 36 64

   * - Option
     - Description
   * - ``-bts, --batstatsfile <file>``
     - Recorded ``batterystats`` log.
   * - ``-pft, --perfettofile <file>``
     - Recorded Perfetto trace.
   * - ``-htf, --hunterfile <file>``
     - Recorded Hunter log.
   * - ``-d, --directory <dir>``
     - Directory with ``bstats-*``, ``trace-*`` and ``method_traces-*`` files.

The options of the diagram command (``python3 -m manafa.sunburst``) are in :doc:`sunburst`.
