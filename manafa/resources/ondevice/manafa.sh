#!/system/bin/sh
# E-MANAFA on-device recorder.
#
# Records a profiling session on the Android device, with no computer connected.
# `emanafa ondevice install` puts this script and the Perfetto configs on the device.
# The results are analysed on a computer: `emanafa ondevice export` and
# `emanafa ondevice analyze`.
#
# Usage (in a shell on the device, for example `adb shell`):
#   sh manafa.sh start [mode] [app] [methods]
#   sh manafa.sh stop
#   sh manafa.sh status
#   sh manafa.sh clean
#
#   mode     energy (default), memory, both or legacy
#   app      package of the app to profile (optional)
#   methods  none (default), am (method trace of the app) or perf (call-stack samples)
#
# The script does the same steps as the E-MANAFA services on the computer, in the same order.

DIR=$(cd "$(dirname "$0")" && pwd)
RESULTS="$DIR/results"
STATE="$DIR/session"
TRACE="/data/misc/perfetto-traces/emanafa_ondevice"
SAMPLING=750    # sampling period of the method trace, as in AmProfilerService

boot_time() {
    while read -r key value rest; do
        [ "$key" = "btime" ] && echo "$value"
    done < /proc/stat
}

# time of one log line on a clock of logcat (epoch or monotonic)
log_time() {
    set -- $(logcat -d -v "$1" -s EMANAFA_CLOCK -t 1 2> /dev/null | grep EMANAFA_CLOCK)
    echo "$1"
}

state() {
    grep "^$1=" "$STATE" | head -1 | cut -d= -f2-
}

start() {
    mode=${1:-energy}
    app=$2
    methods=${3:-none}
    if [ -f "$STATE" ]; then
        echo "A session is running. Stop it first: sh $0 stop"
        exit 1
    fi
    if [ "$methods" != "none" ] && [ -z "$app" ]; then
        echo "methods=$methods needs the package of an app"
        exit 1
    fi
    # same fallback as the computer: no power rails -> legacy model
    if [ "$(cat "$DIR/power_rails" 2>/dev/null)" = "0" ]; then
        [ "$mode" = "energy" ] && mode=legacy && echo "No power rails on this device: mode legacy"
        [ "$mode" = "both" ] && mode=memory && echo "No power rails on this device: mode memory"
    fi
    case "$mode" in
        energy|memory|both|legacy) ;;
        *) echo "Unknown mode: $mode"; exit 1 ;;
    esac
    if [ "$methods" = "perf" ] && [ "$mode" != "energy" ]; then
        echo "methods=perf needs mode energy"
        exit 1
    fi
    mkdir -p "$RESULTS/perfetto" "$RESULTS/batterystats" "$RESULTS/am"

    # batterystats has no history when the battery is full. unplug it virtually
    unplugged=0
    if dumpsys battery | grep -q "^  level: 100"; then
        dumpsys battery unplug && unplugged=1
    fi

    dumpsys batterystats --reset > /dev/null
    setprop persist.traced.enable 1
    rm -f "$TRACE"
    # perfetto cannot read a file of this directory. it can read a pipe
    if [ "$mode" = "legacy" ]; then
        cat "$DIR/config_legacy.bin" | perfetto --background -o "$TRACE" --config - > /dev/null
    elif [ "$methods" = "perf" ]; then
        sed "s/__APP__/$app/" "$DIR/config_energy_perf.pbtxt" | perfetto --background --txt -o "$TRACE" -c - > /dev/null
    else
        cat "$DIR/config_$mode.pbtxt" | perfetto --background --txt -o "$TRACE" -c - > /dev/null
    fi
    if [ $? -ne 0 ]; then
        echo "Perfetto did not start"
        exit 2
    fi

    run=$(date +%s)
    if [ "$methods" = "am" ]; then
        activity=$(cmd package resolve-activity --brief "$app" | grep "^$app/" | head -1)
        if [ -z "$activity" ]; then
            echo "No launcher activity for $app"
            killall perfetto 2> /dev/null
            exit 3
        fi
        am_trace="$RESULTS/am/app_${app}_${run}_$(boot_time).trace"
        am start -S --start-profiler "$am_trace" --sampling $SAMPLING -n "$activity" > /dev/null
        sleep 1
        am profile stop "$app"
        sleep 1
        # --streaming: without it the trace is empty after about 15 s
        am profile start --streaming --sampling $SAMPLING "$app" "${am_trace%.trace}_exec.trace"
    fi

    {
        echo "mode=$mode"
        echo "app=$app"
        echo "methods=$methods"
        echo "start=$run"
        echo "unplugged=$unplugged"
    } > "$STATE"
    echo "E-MANAFA session started (mode $mode). Stop it with: sh $0 stop"
}

stop() {
    if [ ! -f "$STATE" ]; then
        echo "No session is running"
        exit 1
    fi
    mode=$(state mode)
    app=$(state app)
    methods=$(state methods)
    run=$(date +%s)
    boot=$(boot_time)

    dumpsys batterystats --history > "$RESULTS/batterystats/bstats-$run-$boot.log"

    killall perfetto 2> /dev/null
    # perfetto writes the end of the trace after the signal
    count=0
    while ps -A -o USER,NAME | grep -q "^shell .*perfetto" && [ $count -lt 10 ]; do
        sleep 1
        count=$((count + 1))
    done
    sleep 1
    if [ "$mode" = "legacy" ]; then
        cp "$TRACE" "$RESULTS/perfetto/trace-$run-$boot"
    else
        cp "$TRACE" "$RESULTS/perfetto/trace-$run-$boot.perfetto-trace"
    fi

    [ "$methods" = "am" ] && am profile stop "$app"

    log -t EMANAFA_CLOCK sync
    {
        echo "run=$run"
        echo "mode=$mode"
        echo "app=$app"
        echo "methods=$methods"
        echo "start=$(state start)"
        echo "stop=$run"
        echo "boot_time=$boot"
        echo "timezone=$(getprop persist.sys.timezone)"
        echo "utc_offset=$(date +%z)"
        echo "model=$(getprop ro.product.model)"
        echo "clock_epoch=$(log_time epoch)"
        echo "clock_monotonic=$(log_time monotonic)"
        echo "charge_full=$(cat /sys/class/power_supply/battery/charge_full 2> /dev/null)"
    } > "$RESULTS/session-$run.txt"
    dumpsys battery > "$RESULTS/battery-$run.txt"
    dumpsys batterystats --power-profile > "$RESULTS/power_profile-$run.txt" 2> /dev/null

    [ "$(state unplugged)" = "1" ] && dumpsys battery reset
    rm -f "$STATE"
    echo "E-MANAFA session $run saved in $RESULTS"
}

status() {
    if [ -f "$STATE" ]; then
        echo "running: mode $(state mode), started at $(state start)"
    else
        echo "no session is running"
    fi
    ls "$RESULTS"/session-*.txt 2> /dev/null | wc -l | { read -r n; echo "$n saved session(s)"; }
}

clean() {
    killall perfetto 2> /dev/null
    [ -f "$STATE" ] && [ "$(state unplugged)" = "1" ] && dumpsys battery reset
    rm -rf "$RESULTS" "$STATE"
    echo "results removed"
}

command=${1:-start}
[ $# -gt 0 ] && shift
case "$command" in
    start|stop|status|clean) "$command" "$@" ;;
    *) echo "Usage: sh $0 start [energy|memory|both|legacy] [app] [none|am|perf] | stop | status | clean"; exit 1 ;;
esac
