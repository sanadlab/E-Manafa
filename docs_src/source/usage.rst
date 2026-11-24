Measurement scenarios
=====================

Select what to measure with ``-pm`` (``--profile-mode``):

.. list-table::
   :header-rows: 1
   :widths: 16 20 20 44

   * - Mode
     - Energy
     - Memory
     - Use it when
   * - ``energy`` (default)
     - power rails
     - no
     - you want energy with the smallest overhead
   * - ``memory``
     - no
     - ``/proc/meminfo``
     - you want memory only
   * - ``both``
     - power rails
     - ``/proc/meminfo``
     - you want the two results from the same run
   * - ``legacy``
     - legacy model
     - no
     - the device has no power rails

Energy only
-----------

.. code-block:: console

   $ emanafa -a com.example.app -s 30 -pm energy

Memory only
-----------

.. code-block:: console

   $ emanafa -a com.example.app -s 30 -pm memory

Memory is polled each 50 ms. Change this with ``--meminfo-period-ms``.

Energy and memory in separate runs (recommended)
-------------------------------------------------

Memory polling uses CPU time, and this adds energy. For the most accurate energy result,
measure the two in separate runs of the same workload:

.. code-block:: console

   $ emanafa -a com.example.app -cmd "./run-test.sh" -pm energy -o energy.json
   $ emanafa -a com.example.app -cmd "./run-test.sh" -pm memory -o memory.json

Energy and memory in one run
----------------------------

.. code-block:: console

   $ emanafa -a com.example.app -s 30 -pm both

This is quicker, and the two results are from exactly the same execution. The energy result
includes the cost of memory polling.

A device with no power rails
----------------------------

E-MANAFA changes to the legacy model automatically. To use the legacy model on a device that
has power rails:

.. code-block:: console

   $ emanafa -a com.example.app -s 30 --force-legacy

To use your own power profile:

.. code-block:: console

   $ emanafa -a com.example.app -s 30 --force-legacy -p my_power_profile.xml

How long to measure
-------------------

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Option
     - The session stops
   * - ``-s 30``
     - after 30 seconds
   * - ``-cmd "./run-test.sh"``
     - when the command on the host is complete
   * - none
     - when you press a key

A repeatable workload
---------------------

To compare two versions of an app, the workload must be the same in each run. Put the
interaction in a script and give it with ``-cmd``:

.. code-block:: bash

   #!/bin/bash
   # run-test.sh: scroll the list four times
   for i in 1 2 3 4; do
       adb shell input swipe 500 1600 500 600 200
       sleep 2
   done

.. code-block:: console

   $ emanafa -a com.example.app -cmd "./run-test.sh" -o version_a.json

Do each measurement some times and compare the means. One run is not sufficient, because
background work of the device changes between runs.

The device only, with no app
----------------------------

.. code-block:: console

   $ emanafa -s 30

This gives the energy of the device. No app is started and no method is recorded.

An app with no method trace
---------------------------

The method trace makes the app slower. To measure the energy of the app with no trace:

.. code-block:: console

   $ emanafa -a com.example.app -s 30 --trace-methods none

Finer time resolution
---------------------

The power rails are polled each 250 ms. A shorter period gives a better time resolution for
the energy of methods. It does not change the total.

.. code-block:: console

   $ emanafa -a com.example.app -s 30 --battery-poll-ms 50

Parse recorded files again
--------------------------

.. code-block:: console

   # session of the energy, memory or both mode (power rails): only the trace is necessary
   $ emanafa -pft trace-<id>.perfetto-trace -o again.json

   # session of the legacy model
   $ emanafa -bts bstats-<id>.log -pft trace-<id>.systrace -o again.json
   $ emanafa -d <directory with bstats-*, trace-* and method_traces-* files>

This mode needs no device. With no device, give the power profile of the legacy model with
``-p``. The recorded files are in ``manafa/results`` until the next measurement starts.
