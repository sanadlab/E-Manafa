Python library
==============

Energy and memory of a block of code
------------------------------------

.. code-block:: python

   from manafa.emanafa import EManafa

   em = EManafa()
   em.profiler_mode = "both"          # energy / memory / both / legacy
   em.init(clean=True)
   em.start()

   # ... run the workload ...

   em.stop()                          # pulls the files and parses them

   print(em.power_rails_energy["total"], "J")
   print(em.power_rails_energy["by_rail"])
   print(em.memory_stats)

``power_rails_energy`` is ``None`` if the device has no power rails. Then use the legacy
model.

Energy of an interval
---------------------

With power rails, ``em.power_rails`` gives the energy between two times (epoch seconds):

.. code-block:: python

   start, end = em.power_rails.window()
   middle = (start + end) / 2
   all_rails = em.power_rails.energy_between(start, middle)
   cpu_only = em.power_rails.energy_between(start, middle, em.power_rails.cpu_rails())

Legacy model
------------

.. code-block:: python

   em = EManafa()
   em.profiler_mode = "legacy"
   em.init(clean=True)
   em.start()
   # ... run the workload ...
   em.stop()

   begin = em.perf_events.events[0].time
   end = em.perf_events.events[-1].time
   total, per_component, timeline = em.get_consumption_in_between(begin, end)

Energy of methods
-----------------

.. code-block:: python

   from manafa.am_emanafa import AMEManafa

   em = AMEManafa(app_package_name="com.example.app")
   em.profiler_mode = "energy"
   em.init(clean=True)
   em.start()                         # starts the app again, with the profiler
   # ... use the app ...
   em.stop()

   for method, calls in em.app_consumptions.app_traces.items():
       for call in calls.values():
           print(method, call["consumption"])

``manafa.hunter_emanafa.HunterEManafa`` has the same interface, for apps with the Hunter
instrumentation.

Sunburst diagram
----------------

.. code-block:: python

   import json
   from manafa.sunburst import build_sunburst

   with open("results.json") as f:
       session = json.load(f)
   build_sunburst(session, "app.html", hot_total_pct=5, package="com.example.ui")

One session for each object
---------------------------

Make a new ``EManafa`` object for each session. An object that is used for more than one
session keeps events of the earlier sessions.

The full reference is in :doc:`modules`.
