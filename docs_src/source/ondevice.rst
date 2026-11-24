On-device mode
==============

In the normal mode, the computer controls the session through adb: it starts and stops the
services on the device and pulls the files. In the on-device mode, a recorder script on the
device does these steps. The USB cable can be disconnected during the measurement, so the
device does not charge and adb does not consume energy. The computer analyses the recorded
files later, with the same code as the normal mode.

Use this mode when you need a measurement with no cable, or many sessions before one analysis.

Commands
--------

.. code-block:: console

   $ emanafa ondevice install                       # recorder and Perfetto configs to the device
   $ emanafa ondevice start -pm energy -a com.example.app
   # ... disconnect the cable, use the app, connect the cable ...
   $ emanafa ondevice stop
   $ emanafa ondevice export results/               # recorded sessions to the computer
   $ emanafa ondevice analyze results/ --sunburst   # results of each session

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Command
     - Function
   * - ``install``
     - Puts the recorder and the configs in ``/data/local/tmp/emanafa`` on the device. It also
       checks if the device has power rails. ``--meminfo-period-ms`` and
       ``--battery-poll-ms`` set the polling periods of the configs.
   * - ``start``
     - Starts a session. Options: ``-pm`` (``energy``, ``memory``, ``both``, ``legacy``),
       ``-a <package>`` and ``--trace-methods`` (``am``, ``perf``, ``none``).
   * - ``stop``
     - Stops the session and saves its files on the device.
   * - ``status``
     - Tells if a session is running, and the number of saved sessions.
   * - ``export [DIR]``
     - Copies all the saved sessions to ``DIR`` (default ``emanafa_ondevice``).
   * - ``analyze [DIR]``
     - Computes the results of each session in ``DIR``. Options: ``--sunburst``, ``-of csv``,
       ``-p <power profile>``.
   * - ``run [DIR]``
     - All the steps in sequence, with ``-s <seconds>`` or ``-cmd "<command>"``.
   * - ``clean``
     - Removes the saved sessions from the device.

Start and stop on the device
----------------------------

After ``install``, a shell on the device can control the recorder. The computer is not
necessary:

.. code-block:: console

   $ sh /data/local/tmp/emanafa/manafa.sh start energy com.example.app am
   $ sh /data/local/tmp/emanafa/manafa.sh stop
   $ sh /data/local/tmp/emanafa/manafa.sh status

The arguments of ``start`` are the mode, the package of the app and the method source. All
are optional. The shell must have the permissions of the ``shell`` user (``adb shell``,
also through Wi-Fi), because Perfetto, ``batterystats`` and ``am profile`` need them.

Recorded files
--------------

.. list-table::
   :header-rows: 1
   :widths: 42 58

   * - File
     - Content
   * - ``session-<run>.txt``
     - Mode, app, method source, start and stop time, boot time, timezone, device model, and
       the offset between the clocks.
   * - ``perfetto/trace-<run>-<boot time>``
     - Perfetto trace (``.perfetto-trace`` for the modes with power rails).
   * - ``batterystats/bstats-<run>-<boot time>.log``
     - ``batterystats`` history.
   * - ``am/app_<package>_<start>_<boot time>.trace``
     - Method traces of ``am profile``.
   * - ``battery-<run>.txt``, ``power_profile-<run>.txt``
     - Battery properties and power profile of the device.
   * - ``emanafa_<mode>_<run>.json``
     - Results. ``analyze`` writes this file. It has the format of the normal mode
       (see :doc:`output`).

The analysis needs no device: the session files have all the data of the device that the
analysis uses. ``emanafa -d DIR`` also analyses an exported directory.

Agreement with the normal mode
------------------------------

The two modes use the same Perfetto configs and do the same steps in the same sequence. An
automatic test makes sure that the configs on the device are the configs of the normal mode.

The table shows the same workloads measured with the two modes on a Pixel 9 Pro XL
(Android 16), three runs each, in turns, with the cable connected. The values are the mean
and the standard deviation.

.. list-table::
   :header-rows: 1
   :widths: 40 20 20 20

   * - Value
     - Normal mode
     - On-device mode
     - Difference
   * - Busy loop, power rails: mean power
     - 4.90 ± 0.07 W
     - 4.79 ± 0.06 W
     - -2.2 %
   * - Busy loop: CPU energy of the loop process
     - 45.4 ± 0.3 J
     - 44.9 ± 0.5 J
     - -1.1 %
   * - Busy loop, legacy model: mean power
     - 1.496 ± 0.005 W
     - 1.493 ± 0.010 W
     - -0.2 %
   * - Idle, ``both`` mode: mean power
     - 0.436 ± 0.016 W
     - 0.449 ± 0.015 W
     - +3.0 %
   * - Idle, ``both`` mode: used memory
     - 8939 ± 20 MB
     - 8952 ± 21 MB
     - +0.2 %
   * - App start with method trace: mean power
     - 1.153 ± 0.019 W
     - 1.167 ± 0.007 W
     - +1.3 %
   * - App start: CPU energy of the app
     - 9.48 ± 0.12 J
     - 9.13 ± 0.18 J
     - -3.7 %
   * - App start: energy of the root method
     - 9.01 ± 0.33 J
     - 8.73 ± 0.18 J
     - -3.1 %
   * - App start: recorded method calls
     - 225 356
     - 225 490
     - +0.1 %

The differences are of the same size as the variation between runs of one mode.

Limits
------

- The Hunter method source is not available in this mode.
- ``perf`` needs the ``energy`` mode.
- If the device has no power rails, ``energy`` becomes ``legacy`` and ``both`` becomes
  ``memory``, as in the normal mode.
- The trace buffer has a fixed size. In a long session, the scheduler and CPU frequency
  events fill their buffer, and the newer events are not recorded. The power rails have their
  own buffer, so the total energy stays correct, but the energy of each process is then
  available only for the first part of the session.
