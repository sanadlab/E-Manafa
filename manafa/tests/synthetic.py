"""Synthetic inputs with known energy values, for tests that need no device.

The scenario is a device with 2 CPU clusters of 2 cores, described by PROFILE_XML.
All currents are in mA and the battery voltage is 4 V, so 1 mA is 4 mW.
"""
import os

EPOCH = 1700000000        # epoch time (s) of the first power rail sample
BOOT = 5000               # CLOCK_BOOTTIME (s) at EPOCH
MONOTONIC = 4900          # CLOCK_MONOTONIC (s) at EPOCH: the device was suspended for 100 s
SECONDS = 4               # length of the power rail window
VOLTAGE = 4.0

PROFILE_XML = """<?xml version="1.0" encoding="utf-8"?>
<device name="Android">
    <item name="screen.on">100</item>
    <item name="screen.full">200</item>
    <item name="cpu.suspend">1</item>
    <item name="cpu.idle">3</item>
    <item name="cpu.active">5</item>
    <item name="cpu.cluster_power.cluster0">1</item>
    <item name="cpu.cluster_power.cluster1">2</item>
    <array name="cpu.clusters.cores"><value>2</value><value>2</value></array>
    <array name="cpu.core_speeds.cluster0"><value>1000</value><value>2000</value></array>
    <array name="cpu.core_speeds.cluster1"><value>2000</value><value>4000</value></array>
    <array name="cpu.core_power.cluster0"><value>10</value><value>20</value></array>
    <array name="cpu.core_power.cluster1"><value>100</value><value>200</value></array>
    <item name="wifi.controller.idle">31</item>
    <item name="wifi.controller.rx">39</item>
    <item name="wifi.controller.tx">540</item>
    <item name="modem.controller.idle">156</item>
    <item name="modem.controller.rx">145</item>
    <array name="modem.controller.tx"><value>153</value><value>212</value></array>
    <item name="bluetooth.controller.idle">7</item>
    <item name="gps.on">50</item>
    <array name="gps.signalqualitybased"><value>28</value><value>5</value></array>
</device>
"""

# CPU current of the scenario. Second 0-2: only core 0 runs, at 2000 kHz:
#   core_power 20 + cluster_power 1 + cpu.active 5.
# Second 2-4: core 2 (cluster 1, 4000 kHz) runs too: + core_power 200 + cluster_power 2.
CPU_MA_FIRST_HALF = 26.0
CPU_MA_SECOND_HALF = 228.0
SCREEN_MA = 100.0         # screen on, brightness dark
CPU_JOULES = VOLTAGE * (CPU_MA_FIRST_HALF + CPU_MA_SECOND_HALF) / 1000 * (SECONDS / 2)
SCREEN_JOULES = VOLTAGE * SCREEN_MA / 1000 * SECONDS

# scheduler events of the trace. the app runs alone in the first half. in the second half
# another process runs all the time, and a second thread of the app runs for 1 s
APP = "com.app"
APP_PID = 100
OTHER_PID = 200
CPU_WATTS_FIRST_HALF = VOLTAGE * CPU_MA_FIRST_HALF / 1000
CPU_WATTS_SECOND_HALF = VOLTAGE * CPU_MA_SECOND_HALF / 1000
APP_CPU_JOULES = CPU_WATTS_FIRST_HALF * 2 + CPU_WATTS_SECOND_HALF * 1 / 2
OTHER_CPU_JOULES = CPU_WATTS_SECOND_HALF * (1 / 2 + 1)


def write_profile(directory):
    path = os.path.join(directory, "power_profile.xml")
    with open(path, "w") as f:
        f.write(PROFILE_XML)
    return path


def write_batterystats(directory):
    """batterystats history (timezone UTC) with one constant state: awake, screen on."""
    path = os.path.join(directory, "bstats-1-%d.log" % (EPOCH - BOOT))
    with open(path, "w") as f:
        f.write("Battery History (0% used, 1KB used of 4096KB, 1 strings using 1):\n")
        f.write("                    0 (14) RESET:TIME: 2023-11-14-22-13-20\n")  # = EPOCH in UTC
        f.write("                    0 (2) 100 volt=4000 +running +screen brightness=dark\n")
    return path


def write_am_csv(directory, calls):
    """AM method trace CSV. calls: list of (name, begin, end, depth), in seconds after EPOCH."""
    path = os.path.join(directory, "app_test.csv")
    with open(path, "w") as f:
        f.write('"name","ts","dur","depth"\n')
        for name, begin, end, depth in calls:
            ts = int((MONOTONIC + begin) * 1e9)
            f.write('"%s: ()V",%d,%d,%d\n' % (name, ts, int((end - begin) * 1e9), depth))
    return path


def write_trace(directory):
    """Perfetto trace of the scenario: power rails that consume exactly what the power
    profile model predicts, the cpu frequency and cpu idle events, and a clock snapshot.

    Needs the perfetto python package.
    """
    from perfetto.protos.perfetto.trace.perfetto_trace_pb2 import Trace

    def ns(seconds_after_epoch):
        return int((BOOT + seconds_after_epoch) * 1e9)

    trace = Trace()
    packet = trace.packet.add()
    packet.timestamp = ns(0)
    for clock_id, value in ((6, ns(0)), (3, int(MONOTONIC * 1e9)), (1, int(EPOCH * 1e9))):
        clock = packet.clock_snapshot.clocks.add()
        clock.clock_id = clock_id  # 6 = BOOTTIME, 3 = MONOTONIC, 1 = REALTIME
        clock.timestamp = value

    rails = ["S4M_VDD_CPUCL0", "VSYS_PWR_DISPLAY"]
    packet = trace.packet.add()
    packet.timestamp = ns(0)
    for index, name in enumerate(rails):
        descriptor = packet.power_rails.rail_descriptor.add()
        descriptor.index = index
        descriptor.rail_name = name
        descriptor.subsys_name = "test"
        descriptor.sampling_rate = 4
    for step in range(SECONDS * 4 + 1):
        t = step / 4
        cpu_mw = VOLTAGE * (CPU_MA_FIRST_HALF * min(t, 2) + CPU_MA_SECOND_HALF * max(t - 2, 0))
        display_mw = VOLTAGE * SCREEN_MA * t
        packet = trace.packet.add()
        packet.timestamp = ns(t)
        for index, milliwatt_seconds in enumerate((cpu_mw, display_mw)):
            data = packet.power_rails.energy_data.add()
            data.index = index
            data.timestamp_ms = ns(t) // 1000000
            data.energy = int(milliwatt_seconds * 1000) + 123456  # µWs. counters do not start at 0

    bundle = trace.packet.add().ftrace_events
    bundle.cpu = 0
    # (time, cpu, frequency) and (time, cpu, idle state). 4294967295 = the cpu leaves idle
    for cpu, freq in ((0, 2000), (1, 2000), (2, 4000), (3, 4000)):
        event = bundle.event.add()
        event.timestamp = ns(-1)
        event.pid = 0
        event.cpu_frequency.state = freq
        event.cpu_frequency.cpu_id = cpu
    for t, cpu, state in ((-0.5, 1, 0), (-0.5, 2, 1), (-0.5, 3, 1), (2, 2, 4294967295),
                          (SECONDS + 1, 2, 0), (SECONDS + 2, 2, 4294967295)):
        event = bundle.event.add()
        event.timestamp = ns(t)
        event.pid = 0
        event.cpu_idle.state = state
        event.cpu_idle.cpu_id = cpu

    # processes and scheduler events. see APP_CPU_JOULES
    tree = trace.packet.add()
    tree.timestamp = ns(0)
    for pid, name in ((APP_PID, APP), (OTHER_PID, "other")):
        process = tree.process_tree.processes.add()
        process.pid, process.ppid = pid, 1
        process.cmdline.append(name)
    thread = tree.process_tree.threads.add()
    thread.tid, thread.tgid, thread.name = APP_PID + 1, APP_PID, "worker"
    # (cpu, time, thread that stops, thread that starts). 0 = idle
    switches = ((0, 0, 0, APP_PID), (0, 2, APP_PID, OTHER_PID), (0, SECONDS, OTHER_PID, 0),
                (2, 2, 0, APP_PID + 1), (2, 3, APP_PID + 1, 0))
    for cpu in (0, 2):
        bundle = trace.packet.add().ftrace_events
        bundle.cpu = cpu
        for switch_cpu, t, previous, following in switches:
            if switch_cpu != cpu:
                continue
            event = bundle.event.add()
            event.timestamp = ns(t)
            event.pid = previous
            event.sched_switch.prev_pid, event.sched_switch.prev_comm = previous, "t%d" % previous
            event.sched_switch.next_pid, event.sched_switch.next_comm = following, "t%d" % following
            event.sched_switch.prev_state = 1 if previous else 0
            event.sched_switch.prev_prio = event.sched_switch.next_prio = 120

    path = os.path.join(directory, "trace-1-%d.perfetto-trace" % (EPOCH - BOOT))
    with open(path, "wb") as f:
        f.write(trace.SerializeToString())
    return path
