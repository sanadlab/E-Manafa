Energy of each method
=====================

With ``-a <package>``, E-MANAFA records the methods of the app and gives each one its energy.

.. code-block:: console

   $ emanafa -a com.example.app -s 30

Method trace sources
--------------------

.. list-table::
   :header-rows: 1
   :widths: 24 40 36

   * - ``--trace-methods``
     - How
     - Needs
   * - ``am`` (default with ``-a``)
     - sampled method trace of ``am profile``
     - ``-a``, and a debuggable or profileable app
   * - ``hunter`` (or ``-ht``)
     - log lines of the Hunter instrumentation
     - an instrumented app
   * - ``none``
     - no method trace
     - nothing

The app must permit profiling
-----------------------------

``am profile`` works only if the app is ``debuggable`` (a debug build), or if its manifest has:

.. code-block:: xml

   <application>
       <profileable android:shell="true" />
   </application>

How the trace is recorded
-------------------------

1. E-MANAFA starts the app again with the start-up profiler (``am start -S --start-profiler``).
   This records the start of the app.
2. After 1 second it stops that trace and starts a second one (``am profile start
   --streaming``) for the remaining part of the session.
3. At the end it pulls the two traces and converts them to one CSV file with the columns
   ``name``, ``ts``, ``dur`` and ``depth``.

The trace is sampled each 750 microseconds. A method that is shorter than the sampling period
can be absent.

What a method gets
------------------

- **Power rails:** the energy that the CPU rails measured while the call was on the stack.
  The result also has ``all_rails``, the energy of all the rails in the same interval.
- **Legacy model:** the estimate of the model for the same interval, for each component.
- A call outside the recorded energy data gets no energy.

Read the result with care:

- A caller includes the energy of its callees. Do not add the values of a caller and of its
  callee.
- Calls on different threads at the same time get the same energy. The sum of all methods
  can be larger than the session total.
- The energy is the energy of the device in the interval. Close other apps before a session.

The :doc:`sunburst` solves the first two points: it gives each call path only the energy of
its own code, and divides the energy between methods that run at the same time.

Result files
------------

- ``<output>.json``: section ``method_invocations`` has one record for each call. See
  :doc:`output`.
- ``<output>_calls.csv``: the method trace, which the sunburst diagram reads.
