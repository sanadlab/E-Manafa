Output files
============

Summary in the terminal
-----------------------

Each run prints the total energy (J and Wh), the rails that consume the most, the memory
statistics, and the estimated battery drain.

Result file
-----------

``-o <file>`` sets the result file, and ``-of csv`` writes CSV. The default is a JSON file
with the name ``emanafa_<mode>_<time>.json``.

.. code-block:: json

   {
     "mode": "energy",
     "app": "com.example.app",
     "duration_seconds": 30,
     "energy": {
       "total": 14.12,
       "by_rail": { "power.rails.cpu.big": 4.81, "power.rails.display": 2.45 },
       "start_time": 1795073446.89,
       "measured_duration_seconds": 29.75,
       "method_rails": ["power.rails.cpu.big", "power.rails.cpu.little"],
       "method_energy_series": [[542054935451984, 0.0], [542055185451990, 0.12]]
     },
     "memory": {
       "MemAvailable": { "min_mb": 3201.4, "avg_mb": 3320.1, "max_mb": 3450.0, "samples": 600 }
     },
     "method_invocations": {
       "total": 1842,
       "distinct": 217,
       "trace_file": "results_calls.csv",
       "methods": {
         "com.example.app.MainActivity.onCreate_<id>": {
           "0": { "begin_time": 1795073447.1, "end_time": 1795073447.6,
                  "consumption": 0.81, "cpu": 0.81, "all_rails": 1.02 }
         }
       }
     },
     "battery_drain": {
       "consumed_energy_joules": 14.12,
       "full_capacity_mah": 5060.0,
       "battery_drain_percentage": 0.083
     }
   }

Fields
------

.. list-table::
   :header-rows: 1
   :widths: 38 62

   * - Field
     - Meaning
   * - ``energy.total``
     - Energy of all the rails, in joules.
   * - ``energy.by_rail``
     - Energy of each rail, in joules.
   * - ``energy.measured_duration_seconds``
     - Time that the rail samples cover. It is longer than ``duration_seconds``, because it
       includes the start and the stop of the services.
   * - ``energy.method_rails``
     - Rails that are attributed to methods (the CPU rails).
   * - ``energy.method_energy_series``
     - Cumulative energy of these rails: pairs of time (``CLOCK_MONOTONIC``, nanoseconds)
       and joules. The sunburst diagram reads it.
   * - ``memory.<counter>``
     - Minimum, mean and maximum of a ``/proc/meminfo`` counter, in MB.
   * - ``method_invocations.methods``
     - One record for each call of each method: times (epoch seconds) and energy (J).
       The signature of the method (``method_def``) is only in its first call. A call has
       no fields for components with no energy.
   * - ``battery_drain.full_capacity_mah``
     - Full capacity that the device reports (kernel ``charge_full``, or an estimate from the
       charge counter and the battery level).
   * - ``battery_drain.battery_drain_percentage``
     - Session energy as a percentage of the energy of a full battery.

With the legacy model, the ``energy`` section has the estimate for each component (``cpu``,
``screen``, ``wifi``, and others) in the place of the rails.

.. warning::

   With a method trace, the JSON file has one record for each call. For a session of 20 s it
   can be some tens of MB.

Raw files
---------

The raw files are in ``manafa/results/``. Each new run deletes the files of the previous run.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - File
     - Content
   * - ``perfetto/trace-<id>.perfetto-trace``
     - Perfetto trace. Open it in https://ui.perfetto.dev.
   * - ``batterystats/bstats-<id>.log``
     - ``batterystats`` history.
   * - ``am/app_<package>_<id>.csv``
     - Method trace (``name``, ``ts``, ``dur``, ``depth``).
   * - ``consumptions/functions_<id>_results.json``
     - Energy of each method call.
   * - ``power_profile_<model>.xml``
     - Power profile that E-MANAFA read from the device. The legacy model uses it.
