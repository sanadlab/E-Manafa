Development
===========

Tests
-----

.. code-block:: console

   # tests that need no device
   $ python3 -m pytest manafa/tests/test_energy_model.py manafa/tests/test_main_cli.py manafa/tests/accuracy

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
   * - ``manafa/sunburst.py``
     - Sunburst diagram.
   * - ``manafa/resources/``
     - Perfetto configurations, power profiles, and trace tools.

Build these pages
-----------------

.. code-block:: console

   $ pip install sphinx sphinx_rtd_theme
   $ cd docs_src
   $ make html
   $ cp -r build/html/. ../docs/

The sources are in ``docs_src/source``. The ``docs`` directory has the built pages that
GitHub Pages publishes.
