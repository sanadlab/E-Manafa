

import re
from enum import Enum
import xml.etree.ElementTree as ET
from manafa.parsing.powerProfile.PowerProfile import PowerProfile
from manafa.utils.Logger import log, LogSeverity
from manafa.utils.Utils import execute_shell_command

x="""import time
import subprocess
def executeShCommand(command):

    pipes = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    std_out, std_err = pipes.communicate()
    output = std_out.decode("utf-8").lower()
    if pipes.returncode != 0:
        err_msg = "%s. Code: %s" % (std_err.decode('utf-8').strip(), pipes.returncode)
        print("error executing command %s" % command)
        print(err_msg)
        return -1    
    elif len(std_err)==0:
        return output


def epochToDate(ts):
	return time.ctime(ts)
###"""

def interpolate(x1: float, x2: float, y1: float, y2: float, x: float):
	"""Performs linear interpolation for x between (x1,y1) and (x2,y2) """
	return ((y2 - y1) * x + x2 * y1 - x1 * y2) / (x2 - x1)  if (x2-x1)>0 else y1
	#print(val)
	#print("---")
	#return val


class CPU_STATE(Enum):
	SUSPEND = "suspend"
	IDLE = "idle"
	AWAKE = "awake"
	ACTIVE = "active"


CPU_IDLE_EXIT = 4294967295  # value of 'state' in a cpu_idle event when the core leaves idle


class PerfettoCPUEvent(object):
	"""Stores information regarding each cpu frequency in a given time.

	A perfetto  cpufreq event information, corresponding to a line in an results output file in systrace format.
	Attributes:
		time: event_time.
		vals: frequency for each cpu of device.
		idle: for each cpu of device, True if the cpu is in an idle state. A cpu without information is not idle.
	"""
	def __init__(self, time=0.0, values=[], idle=[]):
		self.time=time
		self.vals = list(values)
		self.idle = list(idle)

	def __str__(self):
		return "time: %f vals =  %s , idle = %s" % (self.time, str(self.vals), str(self.idle))

	def __repr__(self):
		return str(self)

	def update(self, cpu_id,cpu_freq):
		"""update/insert cpufreq val for each cpu id"""
		if len(self.vals)> cpu_id:
			self.vals[cpu_id]=cpu_freq
		else:
			for x in range(len(self.vals)-1, cpu_id):
				self.vals.append(cpu_freq)

	def set_idle(self, cpu_id, is_idle):
		"""update/insert the idle state of a cpu id"""
		self.idle.extend([False] * (cpu_id + 1 - len(self.idle)))
		self.idle[cpu_id] = is_idle

	def calculate_CPUs_current(self, state, profile):
		"""given a power profile and a cpu state, returns the instantaneous current being consumed by all cpu cores in that state.

		This is the model of the Android power profile: each running core consumes the current of its cluster at its
		frequency (cpu.core_power). Each cluster with a running core adds cpu.cluster_power, and cpu.active is added once.
		When no core is running, the cpus consume cpu.idle.

		Args:
			state: cpu state in CPU_STATE values
			profile: power profile class
		"""
		if state in ["idle", "suspend"]:
			return profile.get_CPU_state_current(state) / 1000
		total = 0
		running_clusters = set()
		for core_id, freq in enumerate(self.vals):
			if core_id < len(self.idle) and self.idle[core_id]:
				continue
			bf, aft = profile.get_CPU_core_speed_pair(core_id, freq)
			total += interpolate(bf[0], aft[0], bf[1], aft[1], freq)
			running_clusters.add(profile.get_CPU_core_cluster(core_id))
		if len(running_clusters) == 0:
			return profile.get_CPU_state_current("idle") / 1000
		return (total + profile.get_CPU_shared_current(running_clusters)) / 1000


class PerfettoCPUfreqParser(object):
	"""Parses cpu frequency updates from a log file obtained with perfetto.
	Attributes:
		power_profile: current device power profile.
		start_time: lower timestamp bound to consider.
		timezone: device timezone.
	"""
	def __init__(self, power_profile=None, start_time=0.0, timezone="EST"):
		self.events = []
		self.first_freqs = {}  # cpu id -> first frequency seen in the trace
		self.start_time = start_time
		self.power_profile = self.load_power_profile(power_profile) if power_profile is not None else {}

	def get_device_current_frequency_vals(self):
		cpu_vals = execute_shell_command("adb shell cat '/sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq' ")[1]
		return cpu_vals.splitlines()

	def start(self):
		start_time = execute_shell_command("adb shell date +%s")[1].strip()
		pce = PerfettoCPUEvent(int(start_time))
		for i, cpu_f in enumerate(self.get_device_current_frequency_vals()):
			pce.update(i, int(cpu_f))
		self.events.append(pce)

	@staticmethod
	def load_power_profile(xml_profile):
		"""Loads power profile from xml_profile filepath.
		Returns:
			object: power profile file. 
		"""
		return PowerProfile(xml_profile)

	def parse_file(self, filename):
		"""parses filename.
		Args:
			filename: path of log file resultant of a profiling session with perfetto.
		"""
		with open(filename, 'r') as filehandle:
			lines = filehandle.read().splitlines()
			self.parse_history(lines)

	def parse_history(self, lines):
		"""parses event from lines.
		Args:
			lines: list of lines from file.
		"""
		parsed = []
		for line in lines:
			if line.startswith("#"):
				continue
			z = re.match(r"^\s*([^\s]+)\-(\d+)\s*\(\s*(\d+|\-+)\) \[(\d+)\] (\d+|\.+) ([0-9]*\.[0-9]+|[0-9]+)\: (.*)?$",line)
			if z is not None:
				time = float(z.groups()[5])
				time += self.start_time
				freq_pair = self.parse_event(z.groups()[6])
				idle_pair = self.parse_idle_event(z.groups()[6])
				if freq_pair is not None:
					# the first frequency seen for a cpu is the best estimate of its frequency before that event
					self.first_freqs.setdefault(freq_pair[0], freq_pair[1])
				parsed.append((time, freq_pair, idle_pair))
			else:
				raise Exception("Error parsing file")
		for time, freq_pair, idle_pair in parsed:
			if freq_pair is not None:
				self.add_event(time, freq_pair[0], freq_pair[1])
			elif idle_pair is not None:
				self.add_idle_event(time, idle_pair[0], idle_pair[1])
		if not any(idle_pair is not None for _, _, idle_pair in parsed):
			log("The trace has no cpu_idle events. All cores are counted as running, "
				"so the reported CPU energy is an upper bound", log_sev=LogSeverity.WARNING)

	def __new_event(self, time):
		"""appends and returns a new event at time, with the cpu values of the last event."""
		if len(self.events) == 0:
			known_cores = sum(self.power_profile.get_CPU_cluster_cores()) if self.power_profile else 0
			n_cores = max([known_cores] + [cpu_id + 1 for cpu_id in self.first_freqs])
			# a frequency of 0 gives the current of the lowest speed in the power profile
			z = PerfettoCPUEvent(time, [self.first_freqs.get(cpu_id, 0) for cpu_id in range(n_cores)])
		else:
			last = self.events[-1]
			z = PerfettoCPUEvent(time, last.vals, last.idle)
		self.events.append(z)
		return z

	def add_event(self, time: float, cpu_id: int, cpu_freq: int):
		"""add or update cpu freq event based on values passed as argument.
		Args:
			time: timestamp of event.
			cpu_id: id of cpu.
			cpu_freq: frequency value.
		"""
		self.__new_event(time).update(cpu_id, cpu_freq)

	def add_idle_event(self, time: float, cpu_id: int, is_idle: bool):
		"""add an event where a cpu enters or leaves an idle state.
		Args:
			time: timestamp of event.
			cpu_id: id of cpu.
			is_idle: True if the cpu enters an idle state.
		"""
		self.__new_event(time).set_idle(cpu_id, is_idle)

	def parse_event(self, ev_str):
		""" parse frequency and cpu id from string.
		Args:
			ev_str: string expecting to have the patttern.
		Returns:
			cpu_id(int): id of the cpu.
			cpu_freq(int): frequency value.
		"""
		mat = re.match(r'cpu_frequency: state=(\d+) cpu_id=(\d+)', ev_str)
		if mat:
			cpu_id = int(mat.groups()[1])
			cpu_freq = int(mat.groups()[0])
			return cpu_id, cpu_freq
		return None

	def parse_idle_event(self, ev_str):
		""" parse cpu id and idle state from string.
		Args:
			ev_str: string expecting to have the patttern.
		Returns:
			cpu_id(int): id of the cpu.
			is_idle(bool): True if the cpu enters an idle state, False if it leaves it.
		"""
		mat = re.match(r'cpu_idle: state=(\d+) cpu_id=(\d+)', ev_str)
		if mat:
			return int(mat.groups()[1]), int(mat.groups()[0]) != CPU_IDLE_EXIT
		return None

	def get_closest_pair(self, time):
		"""return the closest indexes of samples to time.
		Args:
			time: reference time.
		Returns:
			lasti(int): before index.
			i(int): after index.
		"""
		# binary search for the first event after time. cpu_idle events make the list long
		lo, hi = 0, len(self.events)
		while lo < hi:
			mid = (lo + hi) // 2
			if self.events[mid].time > time:
				hi = mid
			else:
				lo = mid + 1
		if lo == len(self.events):
			return max(lo - 1, 0), max(lo - 1, 0)
		return max(lo - 1, 0), lo


def parse_dumpsys_output(dumpsys_text: str) -> dict:
	"""
    Parses the plain text output from the dumpsys command into a dictionary.

    Args:
        dumpsys_text: The multi-line string output from the adb command.

    Returns:
        A dictionary where keys are the power profile items (e.g., 'cpu.active')
        and values are either floats or lists of floats.
    """
	parsed_data = {}
	lines = dumpsys_text.strip().split('\n')

	for line in lines:
		line = line.strip()

		# Stop at special sections that don't follow the key=value format
		if line.lower().startswith('modem values'):
			break

		# Skip lines that are not key-value pairs
		if '=' not in line:
			continue

		key, value_str = line.split('=', 1)
		key = key.strip()
		value_str = value_str.strip()

		# Check if the value is a list (array)
		if value_str.startswith('[') and value_str.endswith(']'):
			# It's a list. Parse its contents.
			list_content = value_str[1:-1]
			if list_content:
				# Split by comma and convert each element to float
				parsed_value = [float(item.strip()) for item in list_content.split(',')]
			else:
				parsed_value = []
		else:
			# It's a single item. Convert to float.
			try:
				parsed_value = float(value_str)
			except ValueError:
				# Skip if the value is not a valid float
				continue

		parsed_data[key] = parsed_value

	return parsed_data


def generate_power_profile_xml(data: dict, output_filename: str):
	"""
    Generates a power_profile.xml file from a parsed data dictionary.

    Args:
        data: The dictionary generated by parse_dumpsys_output.
        output_filename: The name of the file to save (e.g., 'power_profile.xml').
    """
	# Create the root element <device name="Android">
	root = ET.Element("device", name="Android")

	# Sort keys for a consistent output file
	sorted_keys = sorted(data.keys())

	for key in sorted_keys:
		value = data[key]
		if isinstance(value, list):
			# This is an array
			array_element = ET.SubElement(root, "array", name=key)
			for item in value:
				value_element = ET.SubElement(array_element, "value")
				value_element.text = str(item)
		else:
			# This is a single item
			item_element = ET.SubElement(root, "item", name=key)
			item_element.text = str(value)

	# Create an ElementTree object and write it to a file
	tree = ET.ElementTree(root)

	# Use ET.indent for pretty-printing the XML (available in Python 3.9+)
	# This makes the output human-readable.
	ET.indent(tree, space="\t", level=0)

	tree.write(output_filename, encoding="utf-8", xml_declaration=True)
	log(f"Successfully generated '{output_filename}'")


if __name__ == '__main__':
	# --- Paste the output of 'adb shell dumpsys batterystats --power-profile' here ---
	sample_dumpsys_output = """
    Power Profile: 
        ambient.on=32.0 
        wifi.controller.tx=540.0 
        cpu.active=10.62 
        wifi.controller.rx=39.0 
        modem.controller.idle=156.0 
        camera.avg=900.0 
        video=25.0 
        screen.full.display0=470.0 
        camera.flashlight=240.47 
        modem.controller.rx=145.0 
        gps.voltage=3700.0 
        screen.on=98.0 
        modem.controller.sleep=0.0 
        cpu.suspend=9.28 
        audio=75.0 
        wifi.controller.voltage=3850.0 
        modem.controller.voltage=3700.0 
        wifi.controller.idle=31.0 
        screen.on.display0=98.0 
        screen.full=470.0 
        cpu.cluster_power.cluster0=0.0 
        cpu.cluster_power.cluster1=1.12 
        cpu.idle=22.71 
        cpu.cluster_power.cluster2=2.2 
        ambient.on.display0=32.0 
        cpu.core_speeds.cluster2=[700000.0, 1164000.0, 1296000.0, 1557000.0, 1745000.0, 1885000.0, 1999000.0, 2147000.0, 2294000.0, 2363000.0, 2499000.0, 2687000.0, 2802000.0, 2914000.0, 2943000.0, 2970000.0, 3015000.0, 3105000.0] 
        cpu.core_power.cluster0=[1.27, 5.87, 6.5, 7.93, 10.5, 14.06, 16.18, 17.65, 20.92, 24.67, 34.09, 47.41] 
        gps.signalqualitybased=[28.0, 5.0] 
        cpu.core_power.cluster2=[16.91, 29.84, 39.6, 47.85, 58.03, 66.96, 74.66, 87.45, 102.03, 110.83, 130.63, 165.41, 193.74, 229.21, 238.15, 244.34, 251.35, 253.83] 
        modem.controller.tx=[153.0, 212.0, 292.0, 359.0, 471.0] 
        cpu.core_power.cluster1=[5.4, 8.9, 9.9, 12.65, 14.65, 18.15, 22.65, 25.15, 28.65, 33.9, 46.4, 56.9, 71.15, 81.4, 92.9, 102.4, 124.9] 
        cpu.core_speeds.cluster0=[324000.0, 610000.0, 820000.0, 955000.0, 1098000.0, 1197000.0, 1328000.0, 1425000.0, 1548000.0, 1696000.0, 1849000.0, 1950000.0] 
        cpu.core_speeds.cluster1=[357000.0, 578000.0, 648000.0, 787000.0, 910000.0, 1065000.0, 1221000.0, 1328000.0, 1418000.0, 1549000.0, 1795000.0, 1945000.0, 2130000.0, 2245000.0, 2367000.0, 2450000.0, 2600000.0] 
        cpu.clusters.cores=[4.0, 3.0, 1.0] 
        Modem values:
          drain:SLEEP,RAT:DEFAULT=0.0
          drain:IDLE,RAT:DEFAULT=156.0
          drain:RX,RAT:DEFAULT=145.0
          drain:TX,level:0,RAT:DEFAULT=153.0
    """

	# Define the output file name
	output_xml_file = "power_profile.xml"

	# 1. Parse the text output
	parsed_profile_data = parse_dumpsys_output(sample_dumpsys_output)

	# 2. Generate the XML file from the parsed data
	generate_power_profile_xml(parsed_profile_data, output_xml_file)

#bootTime = float ( executeShCommand ("adb shell cat /proc/stat | grep btime | awk '{print $2}'").strip() )
#print(bootTime)
#print(epochToDate(bootTime))
#x = PerfettoCPUfreqParser(bootTime)
#x.parseFile("/Users/ruirua/repos/petra_like/results/perfetto/trace-1605638909.systrace")
#print(x.events)

