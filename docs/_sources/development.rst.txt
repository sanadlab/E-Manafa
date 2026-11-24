Development
===========

Tests
-----

.. code-block:: console

   # tests that need no device
   $ python3 -m pytest manafa/tests/test_*.py manafa/tests/accuracy manafa/tests/parsers manafa/tests/hunter

   # all tests (some need a connected device)
   $ python3 -m pytest manafa/tests

Structure of the code
---------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Path
     - Content
   * - ``manafa/main.py``
     - Command line.
   * - ``manafa/emanafa.py``
     - Session control and energy calculation.
   * - ``manafa/am_emanafa.py``, ``manafa/hunter_emanafa.py``
     - Sessions with a method trace.
   * - ``manafa/services/``
     - Control of the device services: Perfetto, ``batterystats``, ``am profile``, logcat.
   * - ``manafa/parsing/``
     - Parsers of the recorded files, and the power rails calculation.
   * - ``manafa/parsing/cpuAttribution.py``
     - Energy of the CPU rails for each thread and process.
   * - ``manafa/sunburst.py``
     - Sunburst diagram.
   * - ``manafa/ondevice.py``
     - On-device mode: install, export and analysis of recorded sessions.
   * - ``manafa/mcp_server.py``
     - MCP server.
   * - ``manafa/resources/``
     - Perfetto configurations, power profiles, and trace tools.
   * - ``manafa/resources/ondevice/manafa.sh``
     - Recorder script that runs on the device.

Build these pages
-----------------

.. code-block:: console

   $ pip install sphinx sphinx_rtd_theme
   $ cd docs_src
   $ make html
   $ cp -r build/html/. ../docs/

The sources are in ``docs_src/source``. The ``docs`` directory has the built pages that
GitHub Pages publishes.
