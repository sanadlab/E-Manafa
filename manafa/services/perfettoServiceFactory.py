"""Factory for creating appropriate Perfetto service based on device capabilities."""

from .perfettoService import PerfettoService, device_has_perfetto
from .perfettoServiceEnhanced import (
    PerfettoServiceEnhanced, device_supports_power_rails,
    DEFAULT_MEMINFO_PERIOD_MS, DEFAULT_BATTERY_POLL_MS,
)
from ..utils.Logger import log, LogSeverity


def create_perfetto_service(boot_time=0, output_res_folder="perfetto",
                            enable_energy=True, enable_memory=False,
                            force_enhanced=False, force_legacy=False,
                            meminfo_period_ms=DEFAULT_MEMINFO_PERIOD_MS,
                            battery_poll_ms=DEFAULT_BATTERY_POLL_MS):
    """Factory function to create appropriate Perfetto service.
    
    Automatically detects device capabilities and returns either:
    - PerfettoServiceEnhanced: For devices with power.rails.* support (newer devices)
    - PerfettoService: For older devices without power rails support
    
    Args:
        boot_time (float): Timestamp of device's last boot
        output_res_folder (str): Folder where logs will be stored
        enable_energy (bool): Enable energy profiling (power rails)
        enable_memory (bool): Enable memory profiling (system memory)
        force_enhanced (bool): Force use of enhanced service (for testing)
        force_legacy (bool): Force use of legacy service (for compatibility)
    
    Returns:
        PerfettoService or PerfettoServiceEnhanced: Appropriate service instance
    
    Raises:
        Exception: If Perfetto is not available on device
        ValueError: If both energy and memory profiling are enabled simultaneously
    """
    if not device_has_perfetto():
        raise Exception("Perfetto is not available on this device")
    
    #validate: cannot enable both energy and memory simultaneously
    #if enable_energy and enable_memory:
    #raise ValueError("Cannot enable both energy and memory profiling simultaneously. "
    #"Run them separately to avoid overhead skewing results.")
    
    #handle forced modes
    if force_legacy:
        log("Forcing legacy PerfettoService", log_sev=LogSeverity.INFO)
        return PerfettoService(boot_time=boot_time, output_res_folder=output_res_folder)
    
    enhanced_kwargs = {
        'boot_time': boot_time,
        'output_res_folder': output_res_folder,
        'meminfo_period_ms': meminfo_period_ms,
        'battery_poll_ms': battery_poll_ms,
    }

    if force_enhanced:
        log(f"Forcing enhanced PerfettoServiceEnhanced (energy={enable_energy}, memory={enable_memory})",
            log_sev=LogSeverity.INFO)
        return PerfettoServiceEnhanced(enable_energy=enable_energy,
                                       enable_memory=enable_memory, **enhanced_kwargs)

    #auto-detect device capabilities
    #if BOTH energy and memory are requested
    if enable_energy and enable_memory and device_supports_power_rails():
        log("Using PerfettoServiceEnhanced for combined energy and memory profiling",
            log_sev=LogSeverity.INFO)
        return PerfettoServiceEnhanced(enable_energy=True, enable_memory=True, **enhanced_kwargs)
    #if energy is requested, check for power rails support
    elif enable_energy and device_supports_power_rails():
        log("Using PerfettoServiceEnhanced for energy profiling (power rails supported)",
            log_sev=LogSeverity.INFO)
        return PerfettoServiceEnhanced(enable_energy=True, enable_memory=False, **enhanced_kwargs)
    #if memory is requested, use enhanced service
    elif enable_memory:
        log("Using PerfettoServiceEnhanced for memory profiling", log_sev=LogSeverity.INFO)
        return PerfettoServiceEnhanced(enable_energy=False, enable_memory=True, **enhanced_kwargs)
    #default: legacy service if power rails not supported
    else:
        log("Using legacy PerfettoService (power rails not supported)", log_sev=LogSeverity.INFO)
        return PerfettoService(boot_time=boot_time, output_res_folder=output_res_folder)