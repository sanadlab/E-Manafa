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

- **Power rails:** the energy of the CPU rails that the processes of the app consumed while
  the call was on the stack (see :ref:`process-energy`). The result also has ``all_rails``,
  the energy of all the rails and all the processes in the same interval.
  ``energy.method_energy_scope`` in the results is ``app process``. It is ``all processes``
  if the trace has no scheduler events of the app (for example in the ``both`` mode): then a
  call gets the energy of all the CPU rails.
- **Legacy model:** the estimate of the model for the same interval, for each component and
  for all the device.
- A call outside the recorded energy data gets no energy.

Read the result with care:

- A caller includes the energy of its callees. Do not add the values of a caller and of its
  callee.
- Calls on different threads at the same time get the same energy. The sum of all methods
  can be larger than the session total.
- With the legacy model, and when the scope is ``all processes``, the energy is the energy
  of the device in the interval. Close other apps before a session.

The :doc:`sunburst` solves the first two points: it gives each call path only the energy of
its own code, and divides the energy between methods that run at the same time.

Result files
------------

- ``<output>.json``: section ``method_invocations`` has one record for each call. See
  :doc:`output`.
- ``<output>_calls.csv``: the method trace, which the sunburst diagram reads.

.. _process-energy:

Energy of each process and thread
---------------------------------

In the ``energy`` mode the Perfetto trace also has the scheduler events (which thread ran on
which CPU), the names of the processes and the CPU clusters. E-MANAFA uses them to divide the
energy of the CPU rails:

1. The time between two rail samples is one interval (250 ms by default).
2. In each interval, the energy of the rail of a CPU cluster is divided between the threads
   that ran on the CPUs of that cluster, in proportion to their run time.
3. The energy of an interval where no thread ran on a cluster is not attributed
   (``energy.unattributed_cpu_joules``).

If the rail names do not show the cluster (``little``, ``mid``, ``big``), the energy of all
the CPU rails is divided by the total run time.

The buffer of the scheduler events has a fixed size (60 MB), sufficient for some minutes of
high CPU load. If it becomes full, E-MANAFA gives a warning, and the energy of processes and
methods is correct only until that time. The session total is not affected.

The results have ``energy.by_process`` (the 20 processes with the most energy) and, with
``-a``, ``energy.app_process`` with the threads of the app. The energy of the idle state of a
cluster goes to the threads that ran on it.

Call-stack samples (``--trace-methods perf``)
---------------------------------------------

.. code-block:: console

   $ emanafa -a com.example.app -s 30 --trace-methods perf

Perfetto samples the call stacks of the app 100 times each second with ``linux.perf``. The
app does not start again, and the overhead is lower than with ``am``. The app must be
debuggable or profileable.

A sample gets the energy of its thread in its interval, divided by the number of samples of
that thread in that interval. ``sampled_functions`` in the results gives, for each function,
the energy of the samples where it is at the top of the stack (``self_joules``) and where it
is in the stack (``total_joules``).

The stacks have native frames. Java and Kotlin methods that the interpreter runs show as
functions of the ART runtime, so use ``am`` to see the methods of the app.
