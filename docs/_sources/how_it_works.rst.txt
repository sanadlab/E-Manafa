How it works
============

Workflow
--------

.. code-block:: text

           Host (Python + adb)                         Android device
    +-----------------------------+   start    +-------------------------------+
    | 1. init                     | ---------> | Perfetto trace:               |
    |    detect power rails,      |            |   power rails (energy)        |
    |    read the power profile   |            |   /proc/meminfo (memory)      |
    | 2. start                    |            |   CPU frequency and idle      |
    | 3. your workload runs       |            | batterystats history          |
    |    (time, key or command)   |            | method trace of the app       |
    | 4. stop                     | <--------- |   (am profile or Hunter)      |
    +--------------+--------------+ pull files +-------------------------------+
                   |
                   v
    +-----------------------------+      +-----------------------------------+
    | 5. parse                    |      | 6. results                        |
    |    energy for each rail,    | ---> |    summary in the terminal        |
    |    memory statistics,       |      |    JSON or CSV file               |
    |    energy for each method   |      |    sunburst diagram (HTML)        |
    +-----------------------------+      +-----------------------------------+

1. **init.** E-MANAFA records a trace of 1 second to find if the device has power rails. It
   reads the power profile and the timezone of the device.
2. **start.** It resets ``batterystats``, starts a Perfetto trace, and starts the method trace
   if you gave an app.
3. **workload.** The session runs for a time, until a command is complete, or until you press
   a key.
4. **stop.** It stops the services and pulls the files to ``manafa/results/``.
5. **parse.** It calculates the energy, the memory statistics and the energy of each method.
6. **results.** It prints a summary and writes the result files.

Three sources of energy data
----------------------------

E-MANAFA selects the source automatically, in this order:

1. **Power rails**, if the device has them.
2. **Energy consumers**, if the device has no rails but has consumers for its CPU clusters.
3. **Legacy model** (power profile), for all other devices.

Each result names the source that it used: ``energy.source`` is ``power rails`` or ``energy
consumers``, and the report of the legacy model has ``energy_source: power profile``.
Results from different sources are not directly comparable between devices.

Power rails (default)
~~~~~~~~~~~~~~~~~~~~~

Some devices have power sensors on their supply rails (Pixel 6 and later, and other recent
devices). Each rail has a counter of the energy that went through it, in microwatt-seconds.
Perfetto records these counters (data source ``android.power``).

The energy of an interval is the difference between two counter values. E-MANAFA interpolates
between samples, so an interval does not have to start at a sample.

- The session total is the sum of all the rails.
- The energy is **measured by hardware**. No model is necessary.
- The polling period (``--battery-poll-ms``, default 250 ms) changes only the time
  resolution, not the total.

Energy consumers
~~~~~~~~~~~~~~~~

The PowerStats HAL of Android 12 and later can report *energy consumers*: the energy of a
component, such as a CPU cluster, the display or the GPU. A vendor can compute a consumer
from the power rails or from its own model. Perfetto reads them from the HAL (option
``collect_energy_estimation_breakdown`` of ``android.power``), also while the device charges.

E-MANAFA uses the consumers in the place of the rails if the device has no rails but has
consumers of the type ``CPU_CLUSTER``. All that E-MANAFA does with rails then works with
consumers: the session total, the energy of each process, and the energy of each method.

- The session total has only the components that have a consumer. It is not the energy of
  all the device. E-MANAFA gives a warning with the list of the components.
- The values are the values of the vendor. On a device with no rails, there is no reference
  to compare them with.
- E-MANAFA assumes that the consumer with ordinal 0 is the CPU cluster with the lowest
  capacity.

On a Pixel 9 Pro XL, the consumers of the CPU clusters and of the GPU have the same energy as
the matching rails, so they add no data on that device.

Comparison between the sources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In the ``energy`` mode the trace has the data of all the sources that the device has. E-MANAFA
computes the CPU energy of the session with each of them and writes the values to
``energy.cpu_joules_by_source``. If two sources disagree by more than a factor of 2, E-MANAFA
gives a warning. The results always use the source of ``energy.source``.

Example from a Pixel 9 Pro XL with a busy loop: power rails 35.26 J, energy consumers
35.19 J, power profile 8.69 J. The power profile of this device is wrong by a factor of 4.

Legacy model
~~~~~~~~~~~~

For a device with no power rails, E-MANAFA estimates the energy:

.. code-block:: text

   energy = sum over time of ( current of each component state  x  voltage  x  duration )

- ``batterystats`` gives the state of each component (screen on, brightness, Wi-Fi active,
  and others) and the battery voltage.
- Perfetto gives the frequency and the idle state of each CPU core.
- ``power_profile.xml`` gives the current of each state. It comes from the device
  (``dumpsys batterystats --power-profile``), or from the file that you give with ``-p``.

The CPU current is the model of the Android power profile:

.. code-block:: text

   sum of running cores ( cpu.core_power of the cluster at the core frequency )
   + cpu.cluster_power of each cluster that has a running core
   + cpu.active
   (all cores idle: cpu.idle)

The result is an estimate. It is only as good as the power profile. See :doc:`accuracy`.

Memory
------

In the ``memory`` and ``both`` modes, Perfetto polls ``/proc/meminfo`` (data source
``linux.sys_stats``). E-MANAFA gives the minimum, the mean and the maximum of each counter,
for the full device.

Energy of a method
------------------

The method trace gives, for each call, a start time and a duration. E-MANAFA gives a call the
energy of the device in that time interval.

- With power rails, the call gets the energy of the **CPU rails** (the rails with ``cpu`` in
  their name). Other rails, such as the display, are not attributed, because a method does
  not control them directly.
- With the legacy model, the call gets the estimate of the model for the interval.
- A caller includes the energy of its callees.
- The interval contains the work of all threads and processes, not only of the method.

Clocks
------

The data sources use different clocks. E-MANAFA converts all of them to epoch time.

.. list-table::
   :header-rows: 1
   :widths: 30 30 40

   * - Source
     - Clock
     - Conversion
   * - Perfetto trace
     - ``CLOCK_BOOTTIME``
     - The clock snapshot of the trace gives the offset to epoch time.
   * - Method trace (``am profile``)
     - ``CLOCK_MONOTONIC``
     - With power rails: the clock snapshot of the trace. Legacy model: one log line of
       the device that is read on the two clocks.
   * - ``batterystats`` history
     - local time of the device
     - The timezone of the device (``persist.sys.timezone``).

``CLOCK_MONOTONIC`` stops while the device sleeps and ``CLOCK_BOOTTIME`` does not. Thus the
boot time of the device is not a correct base for method times.
