#!/usr/bin/env bash
# Fast, locale-safe detail collector for gusthx.resources popup panel.
# Runs only while the popup panel is open.

export LC_ALL=C

# 1. CPU model & Kernel
cpu_model=$(awk -F': ' '/^model name/ { print $2; exit }' /proc/cpuinfo 2>/dev/null)
[[ -z $cpu_model ]] && cpu_model="Processor"
kernel=$(uname -r 2>/dev/null || echo "Linux")

# 2. GPU frequencies (Intel / AMD / DRM sysfs)
gpu_cur=""
gpu_max=""
gpu_boost=""
for card in /sys/class/drm/card[0-9]; do
  if [[ -r "$card/gt_act_freq_mhz" || -r "$card/gt_cur_freq_mhz" ]]; then
    gpu_cur=$(cat "$card/gt_act_freq_mhz" "$card/gt_cur_freq_mhz" 2>/dev/null | head -n 1)
    gpu_max=$(cat "$card/gt_max_freq_mhz" 2>/dev/null | head -n 1)
    gpu_boost=$(cat "$card/gt_boost_freq_mhz" 2>/dev/null | head -n 1)
    break
  fi
done

# 3. CPU Package Temp (best thermal zone or coretemp)
pkg_temp=""
for h in /sys/class/hwmon/hwmon*; do
  name=$(cat "$h/name" 2>/dev/null)
  if [[ $name == "coretemp" || $name == "k10temp" || $name == "zenpower" || $name == "cpu_thermal" ]]; then
    if [[ -r "$h/temp1_input" ]]; then
      raw=$(cat "$h/temp1_input" 2>/dev/null)
      if [[ $raw =~ ^[0-9]+$ ]]; then
        pkg_temp=$(( raw / 1000 ))
        break
      fi
    fi
  fi
done
if [[ -z $pkg_temp ]]; then
  for z in /sys/class/thermal/thermal_zone*; do
    t=$(cat "$z/type" 2>/dev/null)
    if [[ $t == "x86_pkg_temp" || $t == "TCPU" || $t == "cpu-thermal" ]]; then
      raw=$(cat "$z/temp" 2>/dev/null)
      if [[ $raw =~ ^[0-9]+$ ]]; then
        pkg_temp=$(( raw / 1000 ))
        break
      fi
    fi
  done
fi

printf "META\t%s\t%s\t%s\t%s\t%s\t%s\n" \
  "$cpu_model" "$kernel" "${gpu_cur:-0}" "${gpu_max:-0}" "${gpu_boost:-0}" "${pkg_temp:-0}"

# 4. Per-core frequencies (MHz)
idx=0
for f in /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq; do
  if [[ -r "$f" ]]; then
    khz=$(cat "$f" 2>/dev/null)
    if [[ $khz =~ ^[0-9]+$ ]]; then
      printf "FREQ\t%d\t%d\n" "$idx" "$(( khz / 1000 ))"
      idx=$(( idx + 1 ))
    fi
  fi
done
if (( idx == 0 )); then
  awk -F': ' '/^cpu MHz/ { printf "FREQ\t%d\t%.0f\n", i++, $2 }' /proc/cpuinfo 2>/dev/null
fi

# 5. Hardware sensors (temperatures, fans, battery power)
for h in /sys/class/hwmon/hwmon*; do
  [[ -d "$h" ]] || continue
  chip=$(cat "$h/name" 2>/dev/null || echo "sensor")
  for tin in "$h"/temp*_input; do
    [[ -r "$tin" ]] || continue
    base="${tin%_input}"
    lbl=$(cat "${base}_label" 2>/dev/null || echo "${base##*/}")
    val=$(cat "$tin" 2>/dev/null)
    if [[ $val =~ ^[0-9]+$ ]] && (( val > 0 )); then
      printf "SENSOR\t%s\t%s\t%.1f\t°C\n" "$chip" "$lbl" "$(awk -v v="$val" 'BEGIN { printf "%.1f", v / 1000 }')"
    fi
  done
  for fin in "$h"/fan*_input; do
    [[ -r "$fin" ]] || continue
    base="${fin%_input}"
    lbl=$(cat "${base}_label" 2>/dev/null || echo "${base##*/}")
    val=$(cat "$fin" 2>/dev/null)
    if [[ $val =~ ^[0-9]+$ ]]; then
      printf "SENSOR\t%s\t%s\t%s\tRPM\n" "$chip" "$lbl" "$val"
    fi
  done
  if [[ -r "$h/in0_input" && -r "$h/curr1_input" ]]; then
    vin=$(cat "$h/in0_input" 2>/dev/null)
    cin=$(cat "$h/curr1_input" 2>/dev/null)
    if [[ $vin =~ ^[0-9]+$ && $cin =~ ^[0-9]+$ ]] && (( vin > 0 )); then
      watts=$(awk -v v="$vin" -v c="$cin" 'BEGIN { printf "%.1f V · %.2f A (%.1f W)", v/1000, c/1000, (v*c)/1000000 }')
      printf "SENSOR\t%s\tBattery\t%s\t\n" "$chip" "$watts"
    fi
  fi
done

# 6. Mounted Disks (merge Btrfs subvolumes on the same device)
df -B1 -T -P -x tmpfs -x devtmpfs -x efivarfs -x squashfs -x overlay 2>/dev/null | awk '
  NR > 1 {
    dev = $1
    fstype = $2
    total = $3
    used = $4
    avail = $5
    pct = $6
    gsub(/%/, "", pct)
    mount = $7
    if (total <= 0) next
    if (dev in seen_idx) {
      idx = seen_idx[dev]
      if (mount == "/home" && mounts[idx] == "/") {
        mounts[idx] = "/ · /home"
      } else if (mount == "/") {
        mounts[idx] = "/ · " mounts[idx]
      }
      next
    }
    count++
    seen_idx[dev] = count
    mounts[count] = mount
    fstypes[count] = fstype
    devs[count] = dev
    totals[count] = total
    useds[count] = used
    avails[count] = avail
    pcts[count] = pct
  }
  END {
    for (i = 1; i <= count; i++) {
      printf "DISK\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
        mounts[i], fstypes[i], devs[i], totals[i], useds[i], avails[i], pcts[i]
    }
  }
'

# 6b. Connected iPhones/iPads (libimobiledevice; cached ~30s to avoid hitting lockdownd every tick)
if command -v idevice_id >/dev/null 2>&1 && command -v ideviceinfo >/dev/null 2>&1; then
  ios_cache="${XDG_RUNTIME_DIR:-/tmp}/gusthx-resources-ios.tsv"
  ios_now=$(date +%s)
  ios_mtime=$(stat -c %Y "$ios_cache" 2>/dev/null || echo 0)
  if (( ios_now - ios_mtime >= 30 )); then
    {
      for udid in $(timeout 2 idevice_id -l 2>/dev/null | sort -u); do
        info=$(timeout 3 ideviceinfo -u "$udid" -q com.apple.disk_usage 2>/dev/null) || continue
        name=$(timeout 3 ideviceinfo -u "$udid" -k DeviceName 2>/dev/null | tr -d '\t\r\n')
        model=$(timeout 3 ideviceinfo -u "$udid" -k ProductType 2>/dev/null | tr -d '\t\r\n')
        kind=iPhone; [[ $model == iPad* ]] && kind=iPad
        awk -v name="${name:-$kind}" -v model="${model:-iOS}" -v kind="$kind" '
          /^TotalDataCapacity:/   { total = $2 }
          /^AmountDataAvailable:/ { avail = $2 }
          END {
            if (total > 0) {
              used = total - avail
              printf "DISK\t%s\tafc\t%s\t%.0f\t%.0f\t%.0f\t%d\n",
                kind " · " name, model, total, used, avail, (used * 100 / total) + 0.5
            }
          }' <<<"$info"
      done
    } > "$ios_cache.tmp" 2>/dev/null && mv -f "$ios_cache.tmp" "$ios_cache"
  fi
  cat "$ios_cache" 2>/dev/null
fi

# 7. Top Processes by CPU (top 10) and by Memory (top 10)
ps -eo pid=,pcpu=,pmem=,rss=,state=,comm= --sort=-pcpu 2>/dev/null | awk '
  NR <= 10 {
    pid = $1; pcpu = $2; pmem = $3; rss = $4; st = $5
    $1 = ""; $2 = ""; $3 = ""; $4 = ""; $5 = ""
    sub(/^[ \t]+/, "", $0)
    printf "PROC_CPU\t%s\t%s\t%s\t%s\t%s\t%s\n", pid, pcpu, pmem, rss, st, $0
  }
'

ps -eo pid=,pcpu=,pmem=,rss=,state=,comm= --sort=-rss 2>/dev/null | awk '
  NR <= 10 {
    pid = $1; pcpu = $2; pmem = $3; rss = $4; st = $5
    $1 = ""; $2 = ""; $3 = ""; $4 = ""; $5 = ""
    sub(/^[ \t]+/, "", $0)
    printf "PROC_MEM\t%s\t%s\t%s\t%s\t%s\t%s\n", pid, pcpu, pmem, rss, st, $0
  }
'
