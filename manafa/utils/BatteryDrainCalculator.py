"""
Battery Drain Calculator for E-MANAFA
Calculates the percentage of battery drained based on energy consumption.
"""

import re
from manafa.utils.Utils import execute_shell_command
from manafa.utils.Logger import log, LogSeverity


class BatteryDrainCalculator:
    """Calculates battery drain percentage from energy consumption in Joules."""

    def __init__(self):
        self.properties = None

    def get_battery_properties(self):
        """
        Fetches and parses battery properties from the device using ADB.

        Returns:
            A dictionary containing battery properties or None if parsing fails.
        """
        log("Querying device for battery properties...", LogSeverity.INFO)

        res, output, err = execute_shell_command("adb shell dumpsys battery")
        if res != 0:
            log(f"Failed to get battery properties: {err}", LogSeverity.WARNING)
            return None

        properties = {}

        # Regex patterns to find the required values
        patterns = {
            'charge_counter_uah': r'harge counter: (\d+)',  # charge that remains now, in microampere-hours (µAh)
            'voltage_mv': r'voltage: (\d+)',
            'temperature_c': r'temperature: (\d+)',
            'level': r'\n\s*level: (\d+)',  # Current battery percentage
        }

        for key, pattern in patterns.items():
            match = re.findall(pattern, output)
            if match:
                properties[key] = int(match[-1])

        # Full charge capacity. The kernel value is the capacity that the fuel gauge learned, so it
        # already includes battery wear. Not all devices let the shell read it.
        res, charge_full, _ = execute_shell_command("adb shell cat /sys/class/power_supply/battery/charge_full")
        if res == 0 and charge_full.strip().isdigit() and int(charge_full) > 0:
            properties['capacity_mah'] = int(charge_full) / 1000
        elif properties.get('charge_counter_uah') and properties.get('level'):
            # estimate: the remaining charge is level % of the full capacity
            properties['capacity_mah'] = properties['charge_counter_uah'] / 1000 * 100 / properties['level']

        # Check if all necessary properties were found
        required_keys = ['voltage_mv', 'capacity_mah']
        if not all(key in properties for key in required_keys):
            log("Could not parse all required battery properties from device", LogSeverity.WARNING)
            log("Battery drain percentage will not be available", LogSeverity.INFO)
            return None

        # Convert temperature to proper Celsius
        if 'temperature_c' in properties:
            properties['temperature_c'] /= 10.0

        self.properties = properties
        return properties

    def calculate_battery_drain(self, energy_joules):
        """
        Calculates battery drain percentage for a given energy consumption.

        Args:
            energy_joules: Energy consumed in Joules

        Returns:
            Dictionary containing battery drain information, or None if calculation fails
        """
        if self.properties is None:
            self.properties = self.get_battery_properties()

        if self.properties is None:
            return None

        # --- 1. Get Device Battery Capacity ---
        full_capacity_mah = self.properties['capacity_mah']
        current_voltage_v = self.properties['voltage_mv'] / 1000.0

        # --- 2. Calculate Total Battery Energy in Watt-hours (Wh) ---
        # Formula: Wh = (mAh * V) / 1000
        total_battery_energy_wh = (full_capacity_mah * current_voltage_v) / 1000.0

        # --- 3. Convert Function's Energy Consumption to Watt-hours (Wh) ---
        # 1 Wh = 3600 Joules
        function_energy_wh = energy_joules / 3600.0

        # --- 4. Calculate the Percentage of Battery Consumed ---
        if total_battery_energy_wh == 0:
            log("Calculated total battery energy is zero, cannot calculate drain percentage", LogSeverity.WARNING)
            return None

        percentage_drained = (function_energy_wh / total_battery_energy_wh) * 100

        # --- 5. Build result dictionary ---
        result = {
            'full_capacity_mah': full_capacity_mah,
            'current_voltage_v': current_voltage_v,
            'total_battery_energy_wh': total_battery_energy_wh,
            'consumed_energy_joules': energy_joules,
            'consumed_energy_wh': function_energy_wh,
            'battery_drain_percentage': percentage_drained
        }

        # Add temperature if available
        if 'temperature_c' in self.properties:
            result['temperature_c'] = self.properties['temperature_c']

        # Add battery level if available
        if 'level' in self.properties:
            result['battery_level_percent'] = self.properties['level']

        return result

    def format_battery_drain_report(self, drain_info):
        """
        Formats battery drain information as a human-readable string.

        Args:
            drain_info: Dictionary returned by calculate_battery_drain()

        Returns:
            Formatted string report
        """
        if drain_info is None:
            return "\n⚠️  Battery drain percentage unavailable (could not read battery properties)\n"

        report = "\n" + "="*70 + "\n"
        report += "BATTERY DRAIN ANALYSIS\n"
        report += "="*70 + "\n"

        report += f"\nDevice Battery Properties:\n"
        report += f"  Full Capacity:   {drain_info['full_capacity_mah']:.2f} mAh\n"
        report += f"  Current Voltage: {drain_info['current_voltage_v']:.3f} V\n"

        if 'temperature_c' in drain_info:
            report += f"  Temperature:     {drain_info['temperature_c']:.1f} °C\n"

        if 'battery_level_percent' in drain_info:
            report += f"  Battery Level:   {drain_info['battery_level_percent']}%\n"

        report += f"  Total Battery Energy: {drain_info['total_battery_energy_wh']:.3f} Wh\n"

        report += f"\nEnergy Consumption:\n"
        report += f"  Consumed: {drain_info['consumed_energy_joules']:.2f} J ({drain_info['consumed_energy_wh']:.6f} Wh)\n"

        report += f"\nBattery Drain:\n"
        report += f"  Estimated Drain: {drain_info['battery_drain_percentage']:.6f}%\n"

        report += "="*70 + "\n"

        return report
