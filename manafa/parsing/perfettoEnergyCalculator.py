
"""Energy calculator for power rails data from Perfetto traces."""

import bisect

from ..utils.Logger import log, LogSeverity


def _load_trace_processor(trace_file):
    """Opens trace_file with TraceProcessor. Returns None if the file does not exist."""
    import os
    from perfetto.trace_processor import TraceProcessor

    if not os.path.exists(trace_file):
        log(f"ERROR: Trace file does not exist: {trace_file}", log_sev=LogSeverity.ERROR)
        return None
    try:
        log("Loading TraceProcessor (this may download the binary on first use)...", log_sev=LogSeverity.INFO)
        return TraceProcessor(trace=trace_file)
    except Exception as e:
        error_msg = str(e)
        if "failed to start" in error_msg.lower() or "download" in error_msg.lower():
            log("TraceProcessor binary download may have failed", log_sev=LogSeverity.ERROR)
            log("To fix this issue:", log_sev=LogSeverity.ERROR)
            log("  1. Check your internet connection", log_sev=LogSeverity.ERROR)
            log("  2. Run: python3 download_trace_processor.py", log_sev=LogSeverity.ERROR)
            log("  3. Or manually download from: https://get.perfetto.dev/trace_processor", log_sev=LogSeverity.ERROR)
        raise


def count_power_rails(trace_file):
    """Returns the number of power rail counters in trace_file."""
    tp = _load_trace_processor(trace_file)
    if tp is None:
        return 0
    try:
        return len(list(tp.query("SELECT DISTINCT name FROM counter_track WHERE name LIKE 'power.%'")))
    finally:
        tp.close()


class PowerRailsTrace(object):
    """Power rail energy counters of a Perfetto trace, as time series.

    Power rail counters are cumulative energy values, so the energy of a time
    interval is the counter at the end minus the counter at the start. The
    counters are sampled every battery_poll_ms. Between two samples the value
    is linearly interpolated, which means that an interval shorter than the
    sampling period gets the mean power of that period.

    All times are Unix epoch seconds on the device clock.

    Attributes:
        rails (dict): rail name -> (times, joules), two lists of the same length.
        monotonic_to_epoch_s (float): seconds to add to a CLOCK_MONOTONIC time
            of the device (the clock of ART method traces) to get epoch seconds.
    """
    def __init__(self, rails, monotonic_to_epoch_s=0.0):
        self.rails = rails
        self.monotonic_to_epoch_s = monotonic_to_epoch_s

    @classmethod
    def from_trace(cls, trace_file):
        """Loads the power rails of trace_file. Returns None if there are none."""
        try:
            tp = _load_trace_processor(trace_file)
            if tp is None:
                return None
            try:
                # trace timestamps are CLOCK_BOOTTIME ns. the clock snapshot gives
                # the offsets to the other clocks of the device.
                offsets = {}
                for row in tp.query("SELECT clock_name, clock_value, ts FROM clock_snapshot ORDER BY snapshot_id"):
                    offsets.setdefault(row.clock_name, row.clock_value - row.ts)
                rails = {}
                query = """
                SELECT t.name AS name, c.ts AS ts, c.value AS value
                FROM counter c JOIN counter_track t ON c.track_id = t.id
                WHERE t.name LIKE 'power.%'
                ORDER BY t.name, c.ts
                """
                if 'REALTIME' not in offsets:
                    log("Trace has no REALTIME clock snapshot. Power rail times can not be "
                        "aligned with other data sources", log_sev=LogSeverity.ERROR)
                boot_to_epoch_ns = offsets.get('REALTIME', 0)
                for row in tp.query(query):
                    times, joules = rails.setdefault(row.name, ([], []))
                    times.append((row.ts + boot_to_epoch_ns) * 1e-9)
                    #values are in microwatt-seconds (1 µWs = 1e-6 J)
                    joules.append(row.value * 1e-6)
            finally:
                tp.close()
            rails = {name: series for name, series in rails.items() if len(series[0]) >= 2}
            if not rails:
                log("No power rails found in trace", log_sev=LogSeverity.WARNING)
                return None
            log(f"Found {len(rails)} power rails", log_sev=LogSeverity.INFO)
            return cls(rails, (boot_to_epoch_ns - offsets.get('MONOTONIC', 0)) * 1e-9)
        except Exception as e:
            import traceback
            log(f"Error calculating energy: {e}", log_sev=LogSeverity.ERROR)
            log(f"Traceback: {traceback.format_exc()}", log_sev=LogSeverity.ERROR)
            return None

    def window(self):
        """Returns (start, end): the interval that all rails cover."""
        return (max(times[0] for times, _ in self.rails.values()),
                min(times[-1] for times, _ in self.rails.values()))

    def cpu_rails(self):
        """Names of the rails that supply CPU clusters (name contains 'cpu')."""
        return sorted(name for name in self.rails if 'cpu' in name.lower())

    @staticmethod
    def _counter_at(times, joules, t):
        if t <= times[0]:
            return joules[0]
        if t >= times[-1]:
            return joules[-1]
        i = bisect.bisect_right(times, t)
        fraction = (t - times[i - 1]) / (times[i] - times[i - 1])
        return joules[i - 1] + fraction * (joules[i] - joules[i - 1])

    def energy_between(self, start_time, end_time, rails=None):
        """Energy (Joules) that the rails consumed between two timestamps.

        Time outside the recorded samples adds no energy.
        Args:
            start_time: begin timestamp (epoch seconds).
            end_time: end timestamp (epoch seconds).
            rails: names of the rails to add. Default: all rails.
        """
        total = 0.0
        for name in (self.rails if rails is None else rails):
            times, joules = self.rails[name]
            total += self._counter_at(times, joules, end_time) - self._counter_at(times, joules, start_time)
        return total

    def method_rails(self):
        """Rails used to attribute energy to methods: the CPU rails, or all rails
        if the CPU rails can not be identified by name."""
        return self.cpu_rails() or sorted(self.rails)

    def summary(self):
        """Session totals, in the format that is written to the results file."""
        start, end = self.window()
        by_rail = {name: joules[-1] - joules[0] for name, (times, joules) in self.rails.items()}
        method_rails = self.method_rails()
        # cumulative energy of the method rails on the clock of the ART method
        # trace (CLOCK_MONOTONIC ns), so the sunburst can use it directly
        times = self.rails[method_rails[0]][0]
        series = [[round((t - self.monotonic_to_epoch_s) * 1e9), self.energy_between(times[0], t, method_rails)]
                  for t in times]
        result = {
            'total': sum(by_rail.values()),
            'by_rail': by_rail,
            'start_time': start,
            'measured_duration_seconds': end - start,
            'method_rails': method_rails,
            'method_energy_series': series,
        }
        log(f"Total Energy: {result['total']:.2f} Joules in {end - start:.1f} s", log_sev=LogSeverity.INFO)
        log("Top Power Consumers:", log_sev=LogSeverity.INFO)
        for rail, energy in sorted(by_rail.items(), key=lambda x: x[1], reverse=True)[:5]:
            log(f"  {rail}: {energy:.2f} J", log_sev=LogSeverity.INFO)
        return result


def calculate_memory_stats(trace_file, app_package=None):
    """Calculate system memory statistics from Perfetto trace.
    
    Returns min, max, and average for system memory counters.
    """
    try:
        import os
        from perfetto.trace_processor import TraceProcessor

        log(f"Attempting to calculate memory stats from: {trace_file}", log_sev=LogSeverity.INFO)

        #check if file exists
        if not os.path.exists(trace_file):
            log(f"ERROR: Trace file does not exist: {trace_file}", log_sev=LogSeverity.ERROR)
            return None

        log(f"File exists, size: {os.path.getsize(trace_file)} bytes", log_sev=LogSeverity.INFO)

        #try to load TraceProcessor
        tp = None
        try:
            log("Loading TraceProcessor for memory stats...", log_sev=LogSeverity.INFO)
            tp = TraceProcessor(trace=trace_file)
            log("TraceProcessor loaded successfully", log_sev=LogSeverity.INFO)
        except Exception as e:
            error_msg = str(e)
            if "failed to start" in error_msg.lower() or "download" in error_msg.lower():
                log("TraceProcessor binary download may have failed", log_sev=LogSeverity.ERROR)
                log("To fix this issue:", log_sev=LogSeverity.ERROR)
                log("  1. Check your internet connection", log_sev=LogSeverity.ERROR)
                log("  2. Run: python3 download_trace_processor.py", log_sev=LogSeverity.ERROR)
                log("  3. Or manually download from: https://get.perfetto.dev/trace_processor", log_sev=LogSeverity.ERROR)
            raise

        if tp is None:
            log("Failed to load TraceProcessor", log_sev=LogSeverity.ERROR)
            return None
        
        #tp = TraceProcessor(trace=trace_file)
        #log("TraceProcessor loaded successfully for memory stats", log_sev=LogSeverity.INFO)
        
        #query for system memory counters
        query = """
        SELECT 
            t.name as counter_name,
            c.value as value_bytes
        FROM counter c
        JOIN counter_track t ON c.track_id = t.id
        WHERE t.name IN ('MemTotal', 'MemFree', 'MemAvailable', 
                         'Buffers', 'Cached', 'Active', 'Inactive')
        ORDER BY t.name, c.ts
        """
        
        result = tp.query(query)
        
        #organize data by counter name
        counters_data = {}
        for row in result:
            counter_name = row.counter_name
            if counter_name not in counters_data:
                counters_data[counter_name] = []
            counters_data[counter_name].append(row.value_bytes)
        
        if not counters_data:
            log("No system memory data found in trace", log_sev=LogSeverity.WARNING)
            return None
        
        log(f"Found memory data for {len(counters_data)} counters", log_sev=LogSeverity.INFO)
        
        #calculate statistics for each counter
        memory_stats = {}
        
        for counter_name, values in counters_data.items():
            if len(values) > 0:
                min_bytes = min(values)
                max_bytes = max(values)
                avg_bytes = sum(values) / len(values)
                
                memory_stats[counter_name] = {
                    'min_mb': min_bytes / (1024 * 1024),
                    'max_mb': max_bytes / (1024 * 1024),
                    'avg_mb': avg_bytes / (1024 * 1024),
                    'samples': len(values)
                }
        
        return memory_stats
        
    except Exception as e:
        import traceback
        log(f"Error calculating memory stats: {e}", log_sev=LogSeverity.ERROR)
        log(f"Traceback: {traceback.format_exc()}", log_sev=LogSeverity.ERROR)
        return None