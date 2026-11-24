Troubleshooting
===============

.. list-table::
   :header-rows: 1
   :widths: 34 33 33

   * - Symptom
     - Cause
     - Solution
   * - ``Perfetto did not start. No energy data can be recorded``
     - Perfetto is not on the device, or a different trace session is active.
     - Stop other traces (``adb shell killall perfetto``) and start again.
   * - The tool uses the legacy model on a device that should have power rails
     - The trace of 1 second at the start had no rail counters.
     - Do a check with ``adb shell perfetto --query | grep android.power``.
   * - ``Method invocations: 0`` or a method trace with no data
     - The app is not debuggable or profileable.
     - Use a debug build, or add ``<profileable android:shell="true"/>``.
   * - ``Unknown process: <package>`` during the start
     - The app stopped (a crash) before the second trace started.
     - Look at ``adb logcat`` for the exception of the app.
   * - ``No method overlaps the energy data in time``
     - The method times and the energy data are on different time bases.
     - Parse the files with the device connected, or use the power rails mode.
   * - ``Unknown timezone``
     - The timezone that you gave is not an IANA name or a UTC offset.
     - Use a value such as ``Europe/Lisbon`` or ``+0100``.
   * - ``Cannot build sunburst: session has no method trace``
     - The session had no ``-a <package>``, or the trace was empty.
     - Measure again with ``-a``.
   * - ``call trace ... is gone``
     - The ``*_calls.csv`` file of the session was moved or deleted.
     - Keep the CSV file in the location that the JSON file gives.
   * - ``--sunburst needs plotly``
     - plotly is not installed.
     - ``pip install plotly``.
   * - The device shows that it does not charge after a session
     - A session stopped abnormally and left the unplugged state on.
     - ``adb shell dumpsys battery reset``.
   * - The JSON file is very large
     - It has one record for each method call.
     - Use a shorter session, or ``--trace-methods none`` if you do not need methods.
