"""Attribution of the energy of the CPU power rails to threads and processes.

The CPU rails give the energy of each CPU cluster, not of each process. The scheduler
events of the same Perfetto trace give the time that each thread ran on each CPU. Between
two rail samples, a thread gets the part of the energy of a cluster that is equal to its
part of the run time on the CPUs of that cluster.

The energy of an interval where no thread ran on a cluster is not attributed.
"""
import bisect

from ..utils.Logger import log, LogSeverity
from .perfettoEnergyCalculator import _load_trace_processor

CLUSTER_NAMES = ('little', 'mid', 'big')


def cluster_rails(cpu_rails, cpu_clusters):
    """Finds which CPUs each CPU rail supplies.

    Args:
        cpu_rails: names of the CPU rails.
        cpu_clusters: dict cpu -> (cluster id, capacity).
    Returns:
        list of (set of cpus, list of rail names). One group with all the CPUs and all
        the rails if the rails cannot be matched with the clusters by name.
    """
    all_cpus = (set(cpu_clusters), list(cpu_rails))
    clusters = {}
    for cpu, (cluster, capacity) in cpu_clusters.items():
        clusters.setdefault((capacity or 0, cluster), set()).add(cpu)
    ordered = [clusters[key] for key in sorted(clusters)]
    if len(ordered) < 2 or len(ordered) > 3:
        return [all_cpus]
    # little = lowest capacity, big = highest capacity
    names = ('little', 'big') if len(ordered) == 2 else CLUSTER_NAMES
    groups = [(cpus, [rail for rail in cpu_rails if name in rail.lower().split('.')])
              for name, cpus in zip(names, ordered)]
    matched = [rail for _, rails in groups for rail in rails]
    if sorted(matched) != sorted(cpu_rails) or any(not rails for _, rails in groups):
        return [all_cpus]
    return groups


class CpuAttribution(object):
    """Energy of the CPU rails for each thread, in the intervals between the rail samples.

    Attributes:
        times (list): limits of the intervals (epoch seconds). Interval i is times[i] to times[i + 1].
        threads (dict): utid -> {'tid', 'name', 'pid', 'process'}.
        thread_energy (dict): utid -> {interval index: joules}.
        thread_cpu_s (dict): utid -> seconds on a CPU.
        unattributed (float): joules of the intervals where no thread ran on a cluster.
        boot_to_epoch_s (float): seconds to add to a trace timestamp to get epoch seconds.
    """
    def __init__(self, times, threads, thread_energy, thread_cpu_s, unattributed=0.0, boot_to_epoch_s=0.0):
        self.times = times
        self.threads = threads
        self.thread_energy = thread_energy
        self.thread_cpu_s = thread_cpu_s
        self.unattributed = unattributed
        self.boot_to_epoch_s = boot_to_epoch_s

    @classmethod
    def from_slices(cls, rails, groups, slices, threads, boot_to_epoch_s=0.0):
        """Builds the attribution.

        Args:
            rails (PowerRailsTrace): the power rails.
            groups: result of cluster_rails.
            slices: iterable of (start, end, cpu, utid), in epoch seconds.
            threads: dict utid -> {'tid', 'name', 'pid', 'process'}.
        """
        times = rails.rails[groups[0][1][0]][0]
        group_of_cpu = {cpu: index for index, (cpus, _) in enumerate(groups) for cpu in cpus}
        run = {}  # (group, interval) -> {utid: seconds}
        thread_cpu_s = {}
        for start, end, cpu, utid in slices:
            group = group_of_cpu.get(cpu)
            start, end = max(start, times[0]), min(end, times[-1])
            if group is None or end <= start:
                continue
            thread_cpu_s[utid] = thread_cpu_s.get(utid, 0.0) + end - start
            interval = max(bisect.bisect_right(times, start) - 1, 0)
            while interval < len(times) - 1 and times[interval] < end:
                overlap = min(end, times[interval + 1]) - max(start, times[interval])
                if overlap > 0:
                    threads_of_interval = run.setdefault((group, interval), {})
                    threads_of_interval[utid] = threads_of_interval.get(utid, 0.0) + overlap
                interval += 1
        thread_energy = {}
        attributed = 0.0
        for (group, interval), threads_of_interval in run.items():
            joules = rails.energy_between(times[interval], times[interval + 1], groups[group][1])
            total_run = sum(threads_of_interval.values())
            attributed += joules
            for utid, seconds in threads_of_interval.items():
                energy = thread_energy.setdefault(utid, {})
                energy[interval] = energy.get(interval, 0.0) + joules * seconds / total_run
        all_rails = [rail for _, rail_names in groups for rail in rail_names]
        unattributed = rails.energy_between(times[0], times[-1], all_rails) - attributed
        return cls(times, threads, thread_energy, thread_cpu_s, unattributed, boot_to_epoch_s)

    @classmethod
    def from_trace(cls, trace_file, rails):
        """Reads the scheduler events of trace_file. Returns None if the trace has none,
        or has no CPU rails."""
        cpu_rails = rails.cpu_rails() if rails is not None else []
        if not cpu_rails:
            return None
        tp = _load_trace_processor(trace_file)
        if tp is None:
            return None
        try:
            boot_to_epoch_ns = 0
            for row in tp.query("SELECT clock_value, ts FROM clock_snapshot WHERE clock_name = 'REALTIME' "
                                "ORDER BY snapshot_id LIMIT 1"):
                boot_to_epoch_ns = row.clock_value - row.ts
            end_ns = [row.ts for row in tp.query("SELECT MAX(ts) AS ts FROM sched_slice")][0]
            if end_ns is None:
                return None
            cpu_clusters = {row.cpu: (row.cluster_id, row.capacity)
                            for row in tp.query("SELECT cpu, cluster_id, capacity FROM cpu")}
            threads = {}
            query = """
            SELECT t.utid AS utid, t.tid AS tid, t.name AS name, p.pid AS pid, p.name AS process
            FROM thread t LEFT JOIN process p USING (upid)
            """
            for row in tp.query(query):
                threads[row.utid] = {'tid': row.tid, 'name': row.name or str(row.tid), 'pid': row.pid,
                                     'process': row.process or row.name or str(row.tid)}
            # tid 0 is the idle task of each CPU
            query = """
            SELECT s.ts AS ts, s.dur AS dur, s.cpu AS cpu, s.utid AS utid
            FROM sched_slice s JOIN thread t USING (utid) WHERE t.tid != 0
            """
            slices = [((row.ts + boot_to_epoch_ns) * 1e-9,
                       ((row.ts + row.dur if row.dur >= 0 else end_ns) + boot_to_epoch_ns) * 1e-9,
                       row.cpu, row.utid) for row in tp.query(query)]
        finally:
            tp.close()
        rails_end = rails.rails[cpu_rails[0]][0][-1]
        sched_end = (end_ns + boot_to_epoch_ns) * 1e-9
        if sched_end < rails_end - 2:
            # the buffer of the scheduler events is full (fill_policy DISCARD)
            log(f"The scheduler events stop {rails_end - sched_end:.0f} s before the end of the session: the "
                "trace buffer was full. The energy of processes and methods is correct only until that time",
                log_sev=LogSeverity.WARNING)
        groups = cluster_rails(cpu_rails, cpu_clusters)
        if len(groups) == 1:
            log("The CPU rails do not match the CPU clusters by name. The energy of all the CPU rails "
                "is divided by the total run time", log_sev=LogSeverity.INFO)
            groups = [(set(cpu for _, _, cpu, _ in slices), groups[0][1])]
        return cls.from_slices(rails, groups, slices, threads, boot_to_epoch_ns * 1e-9)

    def thread_joules(self, utid):
        return sum(self.thread_energy.get(utid, {}).values())

    def by_process(self, top=None):
        """Processes with their CPU energy, the highest energy first.

        Returns:
            list of {'process', 'pid', 'cpu_seconds', 'energy_joules'}.
        """
        processes = {}
        for utid, info in self.threads.items():
            if utid not in self.thread_cpu_s:
                continue
            key = (info['process'], info['pid'])
            entry = processes.setdefault(key, {'process': info['process'], 'pid': info['pid'],
                                               'cpu_seconds': 0.0, 'energy_joules': 0.0})
            entry['cpu_seconds'] += self.thread_cpu_s[utid]
            entry['energy_joules'] += self.thread_joules(utid)
        result = sorted(processes.values(), key=lambda entry: entry['energy_joules'], reverse=True)
        return result if top is None else result[:top]

    def utids_of_app(self, package):
        """Threads of the processes of an app: the process name is the package, or package:name."""
        return [utid for utid, info in self.threads.items()
                if info['pid'] is not None and (info['process'] == package or info['process'].startswith(package + ':'))]

    def by_thread(self, utids, top=None):
        """Threads with their CPU energy, the highest energy first."""
        result = [{'thread': self.threads[utid]['name'], 'tid': self.threads[utid]['tid'],
                   'cpu_seconds': self.thread_cpu_s[utid], 'energy_joules': self.thread_joules(utid)}
                  for utid in utids if utid in self.thread_cpu_s]
        result.sort(key=lambda entry: entry['energy_joules'], reverse=True)
        return result if top is None else result[:top]

    def series(self, utids):
        """Cumulative energy of a set of threads.

        Returns:
            times, joules: two lists of the same length. joules[i] is the energy until times[i].
        """
        per_interval = [0.0] * (len(self.times) - 1)
        for utid in utids:
            for interval, joules in self.thread_energy.get(utid, {}).items():
                per_interval[interval] += joules
        cumulative = [0.0]
        for joules in per_interval:
            cumulative.append(cumulative[-1] + joules)
        return self.times, cumulative


def sampled_functions(trace_file, attribution, utids, top=50):
    """Energy of the functions in the call-stack samples (linux.perf) of a set of threads.

    A sample gets the energy of its thread in the interval between two rail samples,
    divided by the number of samples of that thread in that interval.

    Returns:
        dict with 'samples', 'samples_with_stack' and 'functions': list of
        {'function', 'self_samples', 'total_samples', 'self_joules', 'total_joules'}.
        'self' is for the function at the top of the stack, 'total' includes its callees.
        None if the trace has no samples.
    """
    tp = _load_trace_processor(trace_file)
    if tp is None:
        return None
    try:
        utids = set(utids)
        samples = [((row.ts * 1e-9) + attribution.boot_to_epoch_s, row.utid, row.callsite_id)
                   for row in tp.query("SELECT ts, utid, callsite_id FROM perf_sample") if row.utid in utids]
        if not samples:
            return None
        parents = {row.id: (row.parent_id, row.name or '[unknown]') for row in tp.query(
            "SELECT c.id AS id, c.parent_id AS parent_id, f.name AS name "
            "FROM stack_profile_callsite c JOIN stack_profile_frame f ON c.frame_id = f.id")}
    finally:
        tp.close()
    times = attribution.times
    per_cell = {}  # (utid, interval) -> number of samples
    placed = []
    for ts, utid, callsite in samples:
        if not times[0] <= ts <= times[-1]:
            continue
        interval = min(max(bisect.bisect_right(times, ts) - 1, 0), len(times) - 2)
        per_cell[(utid, interval)] = per_cell.get((utid, interval), 0) + 1
        placed.append((utid, interval, callsite))
    functions = {}
    for utid, interval, callsite in placed:
        joules = attribution.thread_energy.get(utid, {}).get(interval, 0.0) / per_cell[(utid, interval)]
        stack = []
        while callsite is not None and callsite in parents:
            callsite, name = parents[callsite]
            stack.append(name)
        stack = stack or ['[no stack]']
        for position, name in enumerate(dict.fromkeys(stack)):
            entry = functions.setdefault(name, {'function': name, 'self_samples': 0, 'total_samples': 0,
                                                'self_joules': 0.0, 'total_joules': 0.0})
            entry['total_samples'] += 1
            entry['total_joules'] += joules
            if position == 0:
                entry['self_samples'] += 1
                entry['self_joules'] += joules
    ranked = sorted(functions.values(), key=lambda entry: entry['self_joules'], reverse=True)
    return {'samples': len(placed), 'samples_with_stack': sum(1 for _, _, callsite in placed if callsite is not None),
            'functions': ranked[:top]}
