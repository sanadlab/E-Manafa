Getting started
===============

Requirements
------------

- A device with Android 9 or later.
- USB debugging or wireless debugging on (*Settings > Developer options*).
- macOS or Linux, with Python 3.8 or later.
- The `Android platform-tools <https://developer.android.com/studio/releases/platform-tools>`_
  (``adb``) in your ``PATH``.

Do a check of the connection:

.. code-block:: console

   $ adb devices
   List of devices attached
   55111FDAS0000H    device

E-MANAFA uses the device that ``adb`` selects. Connect only one device.

Installation
------------

From PyPI:

.. code-block:: console

   $ pip install manafa

From the sources:

.. code-block:: console

   $ git clone https://github.com/greensoftwarelab/E-MANAFA.git
   $ cd E-MANAFA
   $ python -m venv env && source env/bin/activate
   $ pip install -r requirements.txt

The sunburst diagram also needs plotly:

.. code-block:: console

   $ pip install plotly

The examples in these pages use ``emanafa``, the command that ``pip install manafa`` gives.
From the sources, use ``python3 manafa/main.py``.

First measurement
-----------------

Measure the device for 30 seconds:

.. code-block:: console

   $ emanafa -s 30

Measure an app for 30 seconds, with the energy of each method and a sunburst diagram:

.. code-block:: console

   $ emanafa -a com.example.app -s 30 --sunburst app.html

E-MANAFA starts the app again, measures, and then prints a summary:

.. code-block:: text

   ======================================================================
   PROFILING RESULTS
   ======================================================================

    ENERGY CONSUMPTION:
     Total: 25.27 Joules (0.0070 Wh)

     Top Power Rail Consumers:
       power.rails.cpu.big: 5.00 J
       power.rails.cpu.little: 3.35 J
       power.rails.cpu.mid: 3.07 J
       power.rails.display: 2.45 J

   Battery Drain:
     Estimated Drain: 0.026590%

     Method invocations: 338760 calls across 6443 distinct methods

It also writes a JSON file with the details (see :doc:`output`), and ``app.html``
(see :doc:`sunburst`).

USB or Wi-Fi
------------

A USB cable charges the device. E-MANAFA tells Android to behave as unplugged during the
session, but the cable still supplies power. For the most accurate measurements, connect
``adb`` through Wi-Fi and disconnect the cable:

.. code-block:: console

   $ adb tcpip 5555
   $ adb connect <ip of the device>:5555

E-MANAFA uses ``adb`` only before and after the session. The Wi-Fi radio stays on during the
session.

Next steps
----------

- :doc:`usage` has a recipe for each scenario.
- :doc:`how_it_works` explains where the numbers come from.
