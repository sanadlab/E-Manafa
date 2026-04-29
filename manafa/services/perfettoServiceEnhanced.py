from __future__ import absolute_import

import re
import tempfile
from .perfettoService import PerfettoService, device_has_perfetto
import os
import time

from manafa.utils.Utils import execute_shell_command, get_resources_dir
from ..utils.Logger import log, LogSeverity

RESOURCES_DIR = get_resources_dir()

DEFAULT_OUT_DIR = "/data/misc/perfetto-traces"
CONFIG_FILE_ENHANCED = "perfetto_config_power_rails.pbtxt"
DEFAULT_MEMINFO_PERIOD_MS = 50
DEFAULT_BATTERY_POLL_MS = 250


def device_supports_power_rails():
    """Check if device supports power rails data collection.
    
    Power rails data is collected via the android.power data source
    with the collect_power_rails option, not as a separate data source.
    
    Returns:
        bool: True if device supports power rails, False otherwise.
    """
    #use strings command to handle binary output from perfetto
    cmd = "adb shell perfetto --query-raw 2>/dev/null | strings | grep -q 'android.power'"
    res, output, _ = execute_shell_command(cmd)
    
    if res == 0:
        log("Device supports power rails via android.power data source", log_sev=LogSeverity.INFO)
        return True
    
    log("Device does not support android.power data source, falling back to legacy profiler",
        log_sev=LogSeverity.WARNING)
    return False

class PerfettoServiceEnhanced(PerfettoService):
    """Enhanced Perfetto service that uses power.rails.* data sources.
    
    This class extends PerfettoService to use the newer power.rails.* data sources
    available on modern Android devices (Android 10+, Pixel 4+, etc.).
    
    The enhanced profiler provides more accurate and granular power consumption data
    by directly accessing hardware power rails instead of relying on battery counters.
    
    Attributes:
        Inherits all attributes from PerfettoService
        cfg_file (str): Uses perfetto_config_power_rails.pbtxt
    """
    
    # def __init__(self, boot_time=0, output_res_folder="perfetto", enable_memory=True):
    #     """Initialize enhanced Perfetto service with power rails and optional memory profiling.
        
    #     Args:
    #         boot_time: Timestamp of device's last boot
    #         output_res_folder: Folder where logs will be stored
    #         enable_memory: Enable memory profiling (default: True)
    #     """
    #     super().__init__(boot_time=boot_time, output_res_folder=output_res_folder)
        
    #     #choose config based on whether memory profiling is enabled
    #     if enable_memory:
    #         self.cfg_file = "perfetto_config_power_memory.pbtxt"
    #         log("Using config with power rails AND memory profiling", 
    #             log_sev=LogSeverity.INFO)
    #     else:
    #         self.cfg_file = "perfetto_config_power_rails.pbtxt"
    #         log("Using config with power rails only", 
    #             log_sev=LogSeverity.INFO)
        
    #     log(f"Initialized PerfettoServiceEnhanced with config: {self.cfg_file}", 
    #         log_sev=LogSeverity.INFO)

    def __init__(self, boot_time=0, output_res_folder="perfetto",
            enable_energy=True, enable_memory=False,
            meminfo_period_ms=DEFAULT_MEMINFO_PERIOD_MS,
            battery_poll_ms=DEFAULT_BATTERY_POLL_MS):
        """Initialize enhanced Perfetto service.

        Args:
            boot_time: Boot time offset
            output_res_folder: Output folder for traces
            enable_energy: Enable energy profiling (power rails)
            enable_memory: Enable memory profiling (system memory only)
            meminfo_period_ms: /proc/meminfo polling period in ms (default 50)
            battery_poll_ms: battery + power rails polling period in ms (default 250)
        """
        super().__init__(boot_time, output_res_folder)

        #select appropriate config file based on mode
        if enable_energy and enable_memory:
            self.cfg_file = "perfetto_config_both.pbtxt"
        elif enable_energy:
            self.cfg_file = "perfetto_config_power_rails.pbtxt"
        elif enable_memory:
            self.cfg_file = "perfetto_config_memory_only.pbtxt"
        else:
            raise ValueError("Must enable either energy or memory profiling")

        self.enable_energy = enable_energy
        self.enable_memory = enable_memory
        self.meminfo_period_ms = meminfo_period_ms
        self.battery_poll_ms = battery_poll_ms
        self._rendered_config_path = None

    def _render_config(self, source_path):
        """Substitute polling periods into the pbtxt and return a temp file path.

        Keeps the on-disk resources untouched so users can still consume them
        directly. The temp file is cleaned up in stop().
        """
        with open(source_path, 'r') as f:
            cfg = f.read()
        cfg = re.sub(r'meminfo_period_ms:\s*\d+',
                     f'meminfo_period_ms: {self.meminfo_period_ms}', cfg)
        cfg = re.sub(r'battery_poll_ms:\s*\d+',
                     f'battery_poll_ms: {self.battery_poll_ms}', cfg)
        fd, rendered = tempfile.mkstemp(prefix='emanafa_pf_', suffix='.pbtxt')
        with os.fdopen(fd, 'w') as f:
            f.write(cfg)
        return rendered

    def start(self):
        """Start profiling session with enhanced config.

        Uses text-based protobuf config (.pbtxt) with --txt flag for power rails.
        """
        config_path = os.path.join(RESOURCES_DIR, self.cfg_file)
        if self.cfg_file.endswith('.pbtxt'):
            self._rendered_config_path = self._render_config(config_path)
            config_path = self._rendered_config_path
            log(f"Rendered Perfetto config (meminfo_period_ms={self.meminfo_period_ms}, "
                f"battery_poll_ms={self.battery_poll_ms}): {config_path}",
                log_sev=LogSeverity.INFO)
            cmd = f"cat {config_path} | adb shell perfetto " \
                  f"{self.get_switch('background', '-b')} --txt " \
                  f"-o {self.output_filename} -c -"
        else:
            #fall back to binary config
            cmd = f"cat {config_path} | adb shell perfetto " \
                  f"{self.get_switch('background', '-b')} " \
                  f"-o {self.output_filename} {self.get_switch('config', '-c')} -"

        log(f"Starting enhanced perfetto: {cmd}", log_sev=LogSeverity.INFO)
        res, o, e = execute_shell_command(cmd=cmd)
        
        if res != 0 or e.strip() != "":
            log(f"Error starting perfetto: {e}", log_sev=LogSeverity.ERROR)
            return False
        
        return True

    def stop(self, file_id=None):
        """Stops profiling and saves trace in native Perfetto format.
        
        Overrides parent to skip systrace conversion and preserve counter data.
        """
        if file_id is None:
            file_id = execute_shell_command("adb shell date +%s")[1].strip()
        
        #try to kill perfetto
        res, o, e = execute_shell_command("adb shell killall perfetto")
        
        #check if perfetto is still running
        is_running_res, is_running_out, _ = execute_shell_command("adb shell ps | grep perfetto")
        
        #only raise exception if killall failed AND perfetto is still running
        if res != 0 and is_running_res == 0 and 'perfetto' in is_running_out:
            raise Exception("unable to kill Perfetto service")
        
        time.sleep(1)
        
        #save as .perfetto-trace to preserve binary format with counter data
        filename = os.path.join(self.results_dir, f'trace-{file_id}-{self.boot_time}.perfetto-trace')
        res, o, e = execute_shell_command(f"adb pull {self.output_filename} {filename}")
        
        if res != 0:
            raise Exception(f"unable to pull trace file. Attempted to copy {self.output_filename} to {filename}")
        
        log(f"Saved Perfetto trace (binary format): {filename}", log_sev=LogSeverity.INFO)

        if self._rendered_config_path and os.path.exists(self._rendered_config_path):
            try:
                os.remove(self._rendered_config_path)
            except OSError:
                pass
            self._rendered_config_path = None

        return filename