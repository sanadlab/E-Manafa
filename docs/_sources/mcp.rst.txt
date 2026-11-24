MCP server
==========

The MCP server gives E-MANAFA to an LLM client, for example Claude Code or Claude Desktop.
The client can record sessions on a connected device and read their results.

.. code-block:: console

   $ pip install "manafa[mcp]"
   $ python -m manafa.mcp_server results/

``results/`` is a results directory of the :doc:`ondevice`. The default is
``emanafa_ondevice``, or the value of the ``EMANAFA_RESULTS_DIR`` variable.

Configuration of the client:

.. code-block:: json

   {
     "mcpServers": {
       "e-manafa": {
         "command": "python",
         "args": ["-m", "manafa.mcp_server", "/path/to/results"]
       }
     }
   }

Tools
-----

In all the tools, an empty ``session`` is the most recent session.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Tool
     - Result
   * - ``list_sessions``
     - Recorded sessions: run id, mode, app, duration, device.
   * - ``get_session_summary``
     - Total energy, mean power, battery drain, and what other data the session has.
   * - ``get_power_rails``
     - Energy and mean power of each power rail (or of each component, with the legacy model).
   * - ``get_process_energy``
     - Energy of the CPU rails for each process.
   * - ``get_app_threads``
     - Energy of the CPU rails for each thread of the app.
   * - ``get_memory``
     - System memory counters.
   * - ``get_hot_methods``
     - Methods with the most energy in their own code. Needs ``am``.
   * - ``get_sampled_functions``
     - Functions of the call-stack samples with their energy. Needs ``perf``.
   * - ``compare_sessions``
     - Difference of mean power between two sessions, total and for each rail.
   * - ``query_trace``
     - Result of a PerfettoSQL query on the trace of a session (100 rows maximum).
   * - ``build_sunburst``
     - Path of the sunburst diagram of a session.
   * - ``device_status``
     - Connected devices, and the state of the recorder.
   * - ``record_session``
     - Records a new session on the connected device (1 to 600 seconds) and gives its summary.

A session is analysed the first time that a tool reads it. The results file stays in the
results directory.

Each tool is also a Python function of ``manafa.mcp_server``, so you can use the tools with
no MCP client.
