Accuracy and limits
===================

Power rails are measurements
----------------------------

Use the power rails when the device has them. The total is the sum of all the rails that the
device reports. E-MANAFA does a check of its results against a direct query of the Perfetto
trace (see `Accuracy tests`_).

The legacy model is an estimate
-------------------------------

The legacy model is only as good as the power profile of the manufacturer. These are the
errors of the legacy model against the power rails, measured on a Pixel 9 Pro XL (Android 16)
with the power profile of the device:

.. list-table::
   :header-rows: 1
   :widths: 40 20 20 20

   * - Scenario
     - Total
     - CPU
     - Screen
   * - Idle, about 20 s
     - +50 %
     - +38 %
     - +225 %
   * - One busy thread
     - -66 %
     - -75 %
     - +228 %
   * - Four busy threads
     - -61 %
     - -66 %
     - +231 %

The cause is the profile. For example, the profile gives about 1 W for the largest core at
full speed, and the rails measure 4.7 W.

Use legacy results to compare runs on the same device. Do not use them as absolute values.

Limits of method energy
-----------------------

- A method gets the energy of the device during its calls. Other threads and processes are
  included.
- The method trace is sampled. Very short methods can be absent.
- The method trace has an overhead: the app is slower and the CPU consumes more. Compare runs
  that use the same options.
- Only CPU rails are attributed. A method that keeps the display or a radio on does not get
  that energy.

Limits of the legacy model
--------------------------

- An active Wi-Fi or mobile radio is counted at its receive current. ``batterystats`` has no
  transmit time, so uploads are counted too low.
- Sensors and memory are not in the model.
- The session and the ``batterystats`` data are aligned with the timezone of the device. If
  detection fails, give the timezone with ``-t``.

The connection to the device
----------------------------

A USB cable charges the device. E-MANAFA tells Android to behave as unplugged, but the cable
still supplies power, and the charging circuit and the heat can change the result. For the
most accurate measurements, connect ``adb`` through Wi-Fi and disconnect the cable (see
:doc:`getting_started`).

Good practice
-------------

- Close other apps and stop downloads and updates.
- Keep the screen brightness the same in all runs (no automatic brightness).
- Use the same workload in each run (``-cmd`` with a script).
- Do each measurement some times and compare the means.
- Let the device cool down between runs.

Accuracy tests
--------------

``manafa/tests/accuracy`` compares E-MANAFA results with the power rails.

.. code-block:: console

   # synthetic session with a known result, no device needed
   $ python3 -m pytest manafa/tests/accuracy -s

   # measure the connected device for 30 s, then compare the legacy model with the rails
   $ EMANAFA_LIVE_ACCURACY=30 python3 -m pytest manafa/tests/accuracy -s

   # a session that was recorded in energy mode
   $ EMANAFA_TRACE_FILE=manafa/results/perfetto/trace-<id>.perfetto-trace \
     EMANAFA_BTS_FILE=manafa/results/batterystats/bstats-<id>.log \
     EMANAFA_POWER_PROFILE=<power_profile.xml> EMANAFA_TIMEZONE=Europe/Lisbon \
     python3 -m pytest manafa/tests/accuracy -s

The report gives the rails energy, the legacy estimate and the relative error:

.. code-block:: text

   Legacy model against power rails, 15.7 s
                 rails (J)     legacy (J)      error
   total           10.7439        11.2159      +4.4%
   cpu              4.1766         2.2463     -46.2%
   screen           1.9820         6.8142    +243.8%

Set ``EMANAFA_MAX_REL_ERROR`` (for example ``0.3``) to make the test fail above an error
limit.
