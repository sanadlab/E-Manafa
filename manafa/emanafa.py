import bisect
import json
import sys
import time
from manafa.services.batteryStatsService import BatteryStatsService
from manafa.services.service import *
from manafa.services import create_perfetto_service
from manafa.parsing.perfetto.perfettoParser import PerfettoCPUfreqParser, parse_dumpsys_output, \
    generate_power_profile_xml
from manafa.parsing.batteryStats.BatteryStatsParser import BatteryStatsParser
from manafa.utils.Logger import log, LogSeverity
from manafa.utils.Utils import execute_shell_command, mega_find, get_resources_dir, get_results_dir, is_float
from manafa.utils.dateUtils import is_known_timezone

MANAFA_RESOURCES_DIR = get_resources_dir()

DEFAULT_PROFILE = os.path.join(MANAFA_RESOURCES_DIR, "profiles", "power_profile.xml")
DEFAULT_TIMEZONE = "GMT"
# voltage to use when BatteryStats is unavailable (device plugged in). typical for Li-ion batteries
DEFAULT_VOLTAGE = 3.7


def get_last_boot_time(bts_file=None):
    """Retrieves timestamp of device last boot, either from the batterystats output filename that contains that info or
    the device itself.

    Args:
      bts_file(str): filename or filename, whose filename contains the boot time of the respective session where it was
      recorded.

    Returns:
      timestamp(float): secs.ms.
    """
    res, out, err = execute_shell_command(
        "adb shell cat /proc/stat | grep btime | awk '{print $2}'")  # executeShCommand("adb shell cat /proc/stat | grep btime | awk '{print $2}'")
    if res != 0 or len(out) == 0:
        log("no device connected. Assuming Boot time of battery stats file", LogSeverity.WARNING)
        flds = os.path.basename(bts_file).split("-") if bts_file is not None else []
        if len(flds) > 1:
            boot_time = flds[2].replace(".log", "")
            log("Boot time: " + boot_time, LogSeverity.WARNING)
            return int(float(boot_time))
        else:
            log("no device connected. Assuming Boot time 0", LogSeverity.WARNING)
            return 0
        # raise Exception("Invalid boot time ")
    # print("[Warning]: no device connected. Assuming Boot time %d" % boot_time)
    return float(out.strip())



class EManafa(Service):
    """Main class that abstracts all the modules and steps of the profiling procedure

    Attributes:
        resources_dir: directory where aux resources are contained
        power_profile: the power profile to be used in the profiling sessions
        boot_time: device's last boot timestamp
        batterystats: batterystats service
        perfetto: perfetto service
        timezone: device timezone
        unplugged: if the device is not charging
        bat_events: Batterystats parser
        pft_out_file: perfetto service output file
        bts_out_file: batterystats output file
    """
    def __init__(self, power_profile=None, timezone=None, resources_dir=MANAFA_RESOURCES_DIR, dynamic_profile=True):
        super(EManafa, self).__init__()
        self.resources_dir = resources_dir
        self.power_profile = power_profile if power_profile is not None else self.infer_power_profile(dynamic_profile)
        self.boot_time = 0
        log("Power profile file: " + self.power_profile, LogSeverity.INFO)
        self.batterystats = BatteryStatsService()

        #initialize profiler_mode before creating service
        self.profiler_mode = None

        #perfetto sampling overrides (consumed in init()); None = service defaults
        self.meminfo_period_ms = None
        self.battery_poll_ms = None

        #don't create perfetto service yet, wait for init() so profiler_mode can be set first
        self.perfetto = None

        self.timezone = timezone if timezone is not None else self.__infer_timezone()
        self.perf_events = PerfettoCPUfreqParser(self.power_profile, self.boot_time, timezone=self.timezone)
        self.unplugged = False
        self.bat_events = None
        self.power_rails = None
        self.pft_out_file = None
        self.bts_out_file = None
    # def _create_perfetto_service(self):
    #     """Create the appropriate perfetto service based on profiler_mode."""
    #     if self.profiler_mode == 'energy':
    #         self.perfetto = create_perfetto_service(
    #             boot_time=self.boot_time, 
    #             output_res_folder="perfetto",
    #             enable_energy=True,
    #             enable_memory=False
    #         )
    #     elif self.profiler_mode == 'memory':
    #         self.perfetto = create_perfetto_service(
    #             boot_time=self.boot_time,
    #             output_res_folder="perfetto", 
    #             enable_energy=False,
    #             enable_memory=True
    #         )
    #     else:
    #         # Default: create with both enabled or use legacy
    #         self.perfetto = create_perfetto_service(
    #             boot_time=self.boot_time,
    #             output_res_folder="perfetto"
    #         )

    def config(self, **kwargs):
        pass

    def init(self, clean=False):
        """inits inner services and virtually unplugs device if it is fully charged"""
        self.boot_time = get_last_boot_time()
        self.batterystats.init(boot_time=self.boot_time)

        sampling = {}
        if self.meminfo_period_ms is not None:
            sampling['meminfo_period_ms'] = self.meminfo_period_ms
        if self.battery_poll_ms is not None:
            sampling['battery_poll_ms'] = self.battery_poll_ms

        #create perfetto service based on profiler_mode
        if self.profiler_mode == 'legacy':
            self.perfetto = create_perfetto_service(
                boot_time=self.boot_time, output_res_folder="perfetto",
                force_legacy=True, **sampling)
        elif self.profiler_mode == 'energy':
            self.perfetto = create_perfetto_service(
                boot_time=self.boot_time, output_res_folder="perfetto",
                enable_energy=True, enable_memory=False, **sampling)
        elif self.profiler_mode == 'memory':
            self.perfetto = create_perfetto_service(
                boot_time=self.boot_time, output_res_folder="perfetto",
                enable_energy=False, enable_memory=True, **sampling)
        elif self.profiler_mode == 'both':
            self.perfetto = create_perfetto_service(
                boot_time=self.boot_time, output_res_folder="perfetto",
                enable_energy=True, enable_memory=True, **sampling)
        else:
            #default: auto-detect (will use enhanced if supported, otherwise legacy)
            self.perfetto = create_perfetto_service(
                boot_time=self.boot_time, output_res_folder="perfetto", **sampling)

        self.perfetto.init(boot_time=self.boot_time)
        self.validate_start()
        self.unplug_if_fully_charged()
        if clean:
            self.clean()


    def start(self):
        """starts inner services."""
        #create perfetto service if not already created
        if self.perfetto is None:
            self._create_perfetto_service()
        
        self.batterystats.start()
        if not self.perfetto.start():
            raise Exception("Perfetto did not start. No energy data can be recorded")
        self.perf_events.start()

    def stop(self, run_id=None):
        """stops inner services.

        Returns:
            bts_file(str): path to the resultant batterystats log file.
            pf_out_file(str): path to the resultant perfetto log file.
        """
        if run_id is None:
            run_id = execute_shell_command("adb shell date +%s")[1].strip()
        self.bts_out_file = self.batterystats.stop(run_id)
        self.pft_out_file = self.perfetto.stop(run_id)
        log("Perfetto file:  %s" % self.pft_out_file)
        self.parse_results(self.bts_out_file, self.pft_out_file)
        if self.unplugged:
            self.plug_back()
        return self.bts_out_file, self.pft_out_file

    def clean(self):
        """calls clean methods from inner services to clean previous result files."""
        self.batterystats.clean()
        self.perfetto.clean()

    def parse_results(self, bts_file=None, pf_file=None):
        """parses results from output results files of perfetto and batterystats services.
        Args:
            bts_file: batterystats output file. if none, uses self.bts_out_file
            pf_file: perfetto output file. if none, uses self.pft_out_file
        """
        if bts_file is None:
            bts_file = self.bts_out_file
        if pf_file is None:
            pf_file = self.pft_out_file
        if bts_file is None or pf_file is None:
            log("Empty result files", log_sev=LogSeverity.FATAL)
        
        #check if we used the enhanced profiler
        from .services.perfettoServiceEnhanced import PerfettoServiceEnhanced
        if isinstance(self.perfetto, PerfettoServiceEnhanced):
            log("Using power rails energy calculation", log_sev=LogSeverity.INFO)
            #use new power rails-based energy calculation
            from .parsing.perfettoEnergyCalculator import PowerRailsTrace, calculate_memory_stats
            self.power_rails = PowerRailsTrace.from_trace(pf_file)
            self.power_rails_energy = self.power_rails.summary() if self.power_rails is not None else None
            
            #get app package, handle both EManafa and AMEManafa
            app_package = getattr(self, 'app', None) or getattr(self, 'app_package_name', None)
            self.memory_stats = calculate_memory_stats(pf_file, app_package=app_package)
        else:
            log("Using legacy batterystats energy calculation", log_sev=LogSeverity.INFO)
            self.power_rails = None
        
        #continue with existing parsing for other metrics
        self.boot_time = get_last_boot_time(bts_file)
        self.bat_events = BatteryStatsParser(self.power_profile, timezone=self.timezone)
        self.bat_events.parse_file(bts_file)

        #only parse perfetto file with old parser if using legacy profiler
        if not isinstance(self.perfetto, PerfettoServiceEnhanced):
            self.perf_events.start_time = self.boot_time
            self.perf_events.parse_file(pf_file)

    def get_consumption_in_between(self, start_time=0, end_time=sys.maxsize, with_metrics=True):
        """retrieves energy consumption and device events between a timestamp interval.

        Args:
            start_time: begin timestamp.
            end_time: end timestamp.
        Returns:
            total(float): system-level energy consumption.
            per_component(dict): per-component energy consumption.
            metrics(dict): batterystats info containing events occurred during the interval. for each type of event, it
            presents.
            with_metrics: False to get no device events ({}). The events of each interval are slow to collect.
        """

        #try to calculate non-CPU energy from BatteryStats
        #if BatteryStats has no samples (device plugged in), only calculate CPU energy
        try:
            total, per_component = self.calculate_non_cpu_energy(start_time, end_time)
        except Exception as e:
            log(f"BatteryStats has no samples (device may be plugged in): {e}", log_sev=LogSeverity.WARNING)
            log("Calculating CPU energy only from Perfetto CPU frequency events", log_sev=LogSeverity.INFO)
            total = 0
            per_component = {}

        total_cpu = self.calculate_cpu_energy(start_time, end_time)
        metrics = self.bat_events.get_events_in_between(start_time, end_time) if with_metrics and self.bat_events and len(self.bat_events.events) > 0 else {}

        if 'cpu' not in per_component:
            per_component['cpu'] = 0
        per_component['cpu'] += total_cpu

        return total + total_cpu, per_component, metrics

    def get_method_consumption(self, start_time, end_time):
        """retrieves the energy to attribute to a method that ran between two timestamps.

        With power rails, this is the measured energy of the CPU rails in the interval (see
        PowerRailsTrace.method_rails). The energy of all rails is given in per_component['all_rails'],
        but it is not attributed, because a method does not control the screen or the radios.
        Without power rails, this is the power-profile estimate of get_consumption_in_between.

        The interval contains the work of all threads and processes, not only of the method.
        """
        if getattr(self, 'power_rails', None) is None:
            if len(self.perf_events.events) > 0:
                # no energy outside the recorded data. the method trace can stop after the energy data
                start_time = max(start_time, self.perf_events.events[0].time)
                end_time = min(end_time, self.perf_events.events[-1].time)
                if end_time <= start_time:
                    return 0, {'cpu': 0}, {}
            # no device events for each call: a session has many thousands of calls, and the events
            # of the session are in the 'stats' of the report
            return self.get_consumption_in_between(start_time, end_time, with_metrics=False)
        cpu = self.power_rails.energy_between(start_time, end_time, self.power_rails.method_rails())
        return cpu, {'cpu': cpu, 'all_rails': self.power_rails.energy_between(start_time, end_time)}, {}

    def calculate_glob_and_component_consumption(self, last_event, per_component_consumption, delta_time, total):
        """ retrieves the global and per-component consumption of a state that lasts delta_time.
        Args:
            last_event: the event containing the last state.
            per_component_consumption: per_component consumption so far.
            delta_time: duration of the state.
            total: total consumption so far.

        Returns:
            total(float): total consumed (at device level).
            per_component_consumption(dict): consumption per device component.
        """
        tot_curr, comps_curr = last_event.get_current_of_batStatEvent()
        total += tot_curr * (last_event.get_voltage_value()) * delta_time
        for comp, comp_curr in comps_curr.items():
            if comp not in per_component_consumption:
                per_component_consumption[comp] = 0
            if is_float(comp_curr):
                per_component_consumption[comp] += (comp_curr * last_event.get_voltage_value() * delta_time)
        return total, per_component_consumption

    def calculate_non_cpu_energy(self, start_time, end_time):
        """Obtains energy consumption of device between a timestamp interval for every component except cpu. for cpu component,
        it stores only the state recorded in battarystats.
        Args:
            start_time: begin timestamp.
            end_time: end timestamp.

        Returns:
            total: system-level energy consumption without cpu energy consumption.
            per_component: per-component energy consumption without cpu energy consumption.

        """
        c_beg_bef, c_beg_aft = self.bat_events.get_closest_pair(start_time)
        if len(self.bat_events.events) == 0:
            raise Exception("Unable no find batterystats samples. Maybe the profiling session or warm-up time wasn't long enough")
            #log("Unable no find batterystats samples. Maybe the profiling session or warm-up time wasn't long enough")
        total = 0
        per_component_consumption = {}
        last_event = self.bat_events.events[c_beg_bef]
        last_time = start_time  # self.bat_events.events[c_beg_bef].time if c_beg_bef >= 0 else start_time
        if not start_time < self.bat_events.events[c_beg_aft].time <= end_time:
            # batevents |--|--|--|
            # start-end             |--|
            # or in btween two samples
            delta_time = abs(end_time - start_time)
            total, per_component_consumption = self.calculate_glob_and_component_consumption(last_event, per_component_consumption, delta_time, total)
            return total, per_component_consumption
        #
        for i, x in enumerate(self.bat_events.events[c_beg_aft:]):
            if x.time > end_time:
                #delta_time = end_time - last_time
                break
            delta_time = abs(x.time - last_time)

            total, per_component_consumption = self.calculate_glob_and_component_consumption(last_event, per_component_consumption, delta_time, total)
            last_event = x
            last_time = x.time

        delta_time = end_time - last_time
        if delta_time < 0.0:
            log(time=time.time(), message="Error calculating delta (<0) ", log_sev=LogSeverity.FATAL)
        total, per_component_consumption = self.calculate_glob_and_component_consumption(last_event, per_component_consumption,delta_time, total)
        return total, per_component_consumption

    def calculate_cpu_energy(self, start_time, end_time):
        """calculates cpu energy consumption of device between a timestamp interval
        Args:
            start_time: begin timestamp
            end_time: end timestamp
        Returns:
            total: cpu energy consumption
        """
        if len(self.perf_events.events) == 0:
            raise Exception("Unable no find perfetto cpu frequency events. Maybe the profiling session or warm-up time wasn't long enough")
        times, powers, joules = self.cpu_energy_series()

        def energy_at(time):
            # before the first event, the first state is used. after the last event, the last state
            i = max(bisect.bisect_right(times, time) - 1, 0)
            return joules[i] + powers[i] * (time - times[i])
        return energy_at(end_time) - energy_at(start_time)

    def cpu_energy_series(self):
        """Cumulative cpu energy of the session, calculated one time for all the intervals.

        The cpu power changes only at a perfetto cpu event (frequency or idle state) or at a
        batterystats event (cpu state and voltage).
        Returns:
            tuple: times of the changes, cpu power (W) after each time, energy (J) consumed before each time.
        """
        bat_events = self.bat_events.events if self.bat_events is not None else []
        key = (len(self.perf_events.events), len(bat_events))
        if getattr(self, '_cpu_energy_series', (None,))[0] == key:
            return self._cpu_energy_series[1]
        if len(bat_events) == 0:
            log(f"BatteryStats unavailable (device plugged in). Using default voltage {DEFAULT_VOLTAGE}V for CPU energy calculation.",
                log_sev=LogSeverity.INFO)
        cpu_events = self.perf_events.events
        times = sorted(set([x.time for x in cpu_events] + [x.time for x in bat_events]))
        powers = []
        cpu_i, bat_i = 0, 0
        for time in times:
            while cpu_i + 1 < len(cpu_events) and cpu_events[cpu_i + 1].time <= time:
                cpu_i += 1
            while bat_i + 1 < len(bat_events) and bat_events[bat_i + 1].time <= time:
                bat_i += 1
            cpu_event = cpu_events[cpu_i]
            if len(bat_events) > 0:
                state, voltage = bat_events[bat_i].get_cpu_value(), bat_events[bat_i].get_voltage_value()
            else:
                # Infer CPU state from frequencies: if all cores at 0, use idle, otherwise active
                state = "idle" if max(cpu_event.vals, default=0) == 0 else "active"
                voltage = DEFAULT_VOLTAGE
            powers.append(cpu_event.calculate_CPUs_current(state, self.perf_events.power_profile) * voltage)
        joules = [0.0]
        for i in range(1, len(times)):
            joules.append(joules[-1] + powers[i - 1] * (times[i] - times[i - 1]))
        self._cpu_energy_series = (key, (times, powers, joules))
        return self._cpu_energy_series[1]

    def __extract_power_profile(self, filename):
        """ Extracts power_profile.xml file from the device, by pulling framework-res.apk and using apktool to unzip the apk.
        If the process fails, retrieves DEFAULT_PROFILE filepath
        Args:
            filename: the target name of the file
        Returns:
            filename: the name of the extracted xml file
        """
        # first try tro extract power profile via batterystats service
        res, out, v = execute_shell_command("adb shell dumpsys batterystats  --power-profile")
        if res == 0 and not 'unknown option' in out.lower():
            log("Dynamically inferring power profile via batterystats", LogSeverity.INFO)
            parsed_profile_data = parse_dumpsys_output(out)
            # 2. Generate the XML file from the parsed data
            os.makedirs(get_results_dir(), exist_ok=True)
            filename = os.path.join(get_results_dir(), filename)
            generate_power_profile_xml(parsed_profile_data, filename)
            return filename

        # extracting power_profile.xml from device via frawework-res.apk
        res, suc, v = execute_shell_command("adb pull /system/framework/framework-res.apk %s" % self.resources_dir)
        if res == 0:
            cmd = """java -jar {apktooldir} d {fmres} -f -o {outjardir}""".format(
                res_dir=self.resources_dir, apktooldir=os.path.join(self.resources_dir, "apktool_2.12.1.jar"),
                fmres=os.path.join(self.resources_dir, "framework-res.apk"), outjardir=os.path.join(self.resources_dir,
                                                                                                    "out_jar_dir"))
            print(cmd)
            res, suc, v = execute_shell_command(cmd)
            pp_file = os.path.join(self.resources_dir, "out_jar_dir", "res", "xml", "power_profile.xml")
            if res == 0 and os.path.exists(pp_file):
                # cp to profiles, remove out_jar_dir and framework-res.apk
                res, o, u = execute_shell_command(
                    "cp {extracted_file} \"{profiles_dir}\" ; rm -rf {outjardir} {fmres}".format(
                        extracted_file=pp_file, new_file=filename, res_dir=self.resources_dir,
                        profiles_dir=os.path.join(self.resources_dir, "profiles", filename),
                        fmres=os.path.join(self.resources_dir, "framework-res.apk"),
                        outjardir=os.path.join(self.resources_dir,
                                               "out_jar_dir")
                    ))
                if res == 0:
                    return filename
            else:
                log("ERROR while decompiling framework-res.apk to extract power profile. Using default power profile "
                    "instead.", log_sev=LogSeverity.ERROR)

        return DEFAULT_PROFILE

    def infer_power_profile(self, dynamic_profile=True):
        """picks the most appropriate power profile file. power profile files present in self.resources_dir contains a
        device model id in the filename, which is determinated by ro.product.model property. if there is an adequate
        file locally, it retrieves such filename. Otherwise, it extracts the profile from the device
        using __extractPowerProfile.

        Returns:
            filename: the name of the  xml file.
        """
        res, device_model, _ = execute_shell_command("adb shell getprop ro.product.model")
        model_profile_file = """power_profile_{device_model}.xml""".format(
            device_model=device_model.replace(" ", "").strip().lower())
        if dynamic_profile:
            log("Extracting power profile from device")
            power_profile = self.__extract_power_profile(model_profile_file)
            return power_profile
        if  res == 0 and device_model != "":
            matching_profiles = mega_find(self.resources_dir, pattern=model_profile_file, maxdepth=2)
            if len(matching_profiles) > 0:
                return matching_profiles[0]
            else:
                # if power profile not present in profiles directory, extract from device
                log("Extracting power profile from device")
                power_profile = self.__extract_power_profile(model_profile_file)
                return power_profile
        else:
            return DEFAULT_PROFILE

    @staticmethod
    def __infer_timezone():
        """ Obtains device timezone. if there is no device connected, returns DEFAULT_TIMEZONE.
        Returns:
            tz(str): device timezone
        """
        # IANA name of the device timezone, e.g. Europe/Lisbon
        res, out, err = execute_shell_command("adb shell getprop persist.sys.timezone")
        tz = out.strip()
        if res != 0 or not is_known_timezone(tz):
            # fall back to the current UTC offset of the device, e.g. +0100
            res, out, err = execute_shell_command("adb shell date +%z")
            tz = out.strip() if res == 0 and is_known_timezone(out.strip()) else DEFAULT_TIMEZONE
        log("Using timezone: %s" % tz)
        return tz

    def unplug_if_fully_charged(self):
        """ virtually unplugs device charger, by calling dumpsys battery unplug."""
        # battery stats output might be empty when battery level == 100
        res, o, e = execute_shell_command("adb shell dumpsys battery | grep level | grep 100")
        has_full_charge = res == 0 and "100" in o
        if has_full_charge:
            # mock unplug
            res, o, e = execute_shell_command("adb shell dumpsys battery unplug")
            if res == 0:
                self.unplugged = True
                log("virtually unplugging battery charger while running (battery == 100)", LogSeverity.WARNING)

    def plug_back(self):
        """plugs back the device"""
        res, o, e = execute_shell_command("adb shell dumpsys battery reset")
        self.unplugged = False

    def gen_final_report(self, start_time=None, end_time=None):
        if start_time is not None:
            begin = start_time
        elif len(self.perf_events.events) > 0:
            begin = self.perf_events.events[0].time
        else:
            begin = self.bat_events.events[0].time
        if end_time is not None:
            end = end_time
        elif len(self.perf_events.events) > 0:
            end = self.perf_events.events[-1].time
        else:
            end = self.bat_events.events[-1].time
        total, per_c, timeline = self.get_consumption_in_between(begin, end)
        res = {
            'total_energy:': total,
            'elapsed_time': end-begin,
            'per_component_consumption': per_c,
            'stats': timeline,
            'method_invocations': 0,
            'diff_methods': 0
        }
        return {'global': res}

    def save_final_report(self, run_id=None, output_filepath=None):
        if output_filepath is None:
            output_filepath = "manafa_resume_%s.json" % (run_id if run_id is not None else 0)
        with open(output_filepath, 'w') as j:
            json.dump(self.gen_final_report(), j)
        return output_filepath

    def validate_start(self):
        # warn if device is being used via usb
        res, out, err = execute_shell_command("adb devices -l | grep usb")
        if res == 0 and len(out) > 0:
            log("Device is being charged via USB. This might affect the profiling results.", LogSeverity.WARNING)