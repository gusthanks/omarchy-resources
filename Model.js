.pragma library

// Pure data parsers and formatting helpers for gusthx.resources.
// Every function bounds and sanitizes untrusted system/process strings.

var MAX_HISTORY = 60

function neutralizeMarkup(value, maxLen) {
  var limit = (typeof maxLen === "number" && maxLen > 0) ? maxLen : 160
  var s = String(value === undefined || value === null ? "" : value)
  s = s.replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "")
  s = s.replace(/&/g, "\uFF06")
       .replace(/</g, "\uFF1C")
       .replace(/>/g, "\uFF1E")
  if (s.length > limit) {
    s = s.slice(0, limit - 1) + "…"
  }
  return s
}

function clamp(val, minVal, maxVal) {
  var n = Number(val)
  if (!isFinite(n)) return minVal
  return Math.max(minVal, Math.min(maxVal, n))
}

function pushHistory(arr, value, maxLen) {
  var limit = maxLen || MAX_HISTORY
  var next = Array.isArray(arr) ? arr.slice() : []
  next.push(clamp(value, 0, 1e15))
  while (next.length > limit) {
    next.shift()
  }
  return next
}

function parseProcStat(raw, prevState, elapsedSec) {
  var text = String(raw || "")
  var lines = text.split("\n")
  var nextRaw = {
    total: null,
    cores: {},
    ctxt: 0
  }
  var result = {
    cpuPct: 0,
    userPct: 0,
    sysPct: 0,
    iowaitPct: 0,
    cores: [],
    procsRunning: 0,
    procsBlocked: 0,
    ctxtRate: 0,
    rawState: nextRaw
  }

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i].trim()
    if (!line) continue
    var parts = line.split(/\s+/)
    var key = parts[0]

    if (key === "cpu" || /^cpu\d+$/.test(key)) {
      var user = parseInt(parts[1], 10) || 0
      var nice = parseInt(parts[2], 10) || 0
      var sys = parseInt(parts[3], 10) || 0
      var idle = parseInt(parts[4], 10) || 0
      var iowait = parseInt(parts[5], 10) || 0
      var irq = parseInt(parts[6], 10) || 0
      var softirq = parseInt(parts[7], 10) || 0
      var steal = parseInt(parts[8], 10) || 0

      var idleAll = idle + iowait
      var nonIdle = user + nice + sys + irq + softirq + steal
      var total = idleAll + nonIdle

      var snap = {
        user: user + nice,
        sys: sys + irq + softirq,
        iowait: iowait,
        idle: idleAll,
        total: total
      }

      if (key === "cpu") {
        nextRaw.total = snap
      } else {
        nextRaw.cores[key] = snap
      }
    } else if (key === "procs_running") {
      result.procsRunning = parseInt(parts[1], 10) || 0
    } else if (key === "procs_blocked") {
      result.procsBlocked = parseInt(parts[1], 10) || 0
    } else if (key === "ctxt") {
      nextRaw.ctxt = parseInt(parts[1], 10) || 0
    }
  }

  if (nextRaw.total && prevState && prevState.total) {
    var dt = nextRaw.total.total - prevState.total.total
    var didle = nextRaw.total.idle - prevState.total.idle
    var duser = nextRaw.total.user - prevState.total.user
    var dsys = nextRaw.total.sys - prevState.total.sys
    var diow = nextRaw.total.iowait - prevState.total.iowait
    if (dt > 0) {
      result.cpuPct = clamp(((dt - didle) / dt) * 100, 0, 100)
      result.userPct = clamp((duser / dt) * 100, 0, 100)
      result.sysPct = clamp((dsys / dt) * 100, 0, 100)
      result.iowaitPct = clamp((diow / dt) * 100, 0, 100)
    }
  }

  var coreKeys = Object.keys(nextRaw.cores).sort(function(a, b) {
    return (parseInt(a.slice(3), 10) || 0) - (parseInt(b.slice(3), 10) || 0)
  })
  for (var c = 0; c < coreKeys.length; c++) {
    var ck = coreKeys[c]
    var curCore = nextRaw.cores[ck]
    var prevCore = (prevState && prevState.cores) ? prevState.cores[ck] : null
    var pct = 0
    if (curCore && prevCore) {
      var cdt = curCore.total - prevCore.total
      var cdidle = curCore.idle - prevCore.idle
      if (cdt > 0) {
        pct = clamp(((cdt - cdidle) / cdt) * 100, 0, 100)
      }
    }
    result.cores.push({
      id: parseInt(ck.slice(3), 10) || c,
      name: ck.toUpperCase(),
      pct: pct
    })
  }

  if (prevState && prevState.ctxt > 0 && nextRaw.ctxt >= prevState.ctxt && elapsedSec > 0) {
    result.ctxtRate = Math.round((nextRaw.ctxt - prevState.ctxt) / elapsedSec)
  }

  return result
}

function parseProcMeminfo(raw) {
  var text = String(raw || "")
  var lines = text.split("\n")
  var map = {}
  for (var i = 0; i < lines.length; i++) {
    var m = lines[i].match(/^([A-Za-z0-9_()]+):\s+(\d+)/)
    if (m) {
      map[m[1]] = (parseInt(m[2], 10) || 0) * 1024
    }
  }

  var total = map.MemTotal || 0
  var avail = map.MemAvailable !== undefined ? map.MemAvailable : (map.MemFree || 0)
  var used = Math.max(0, total - avail)
  var cached = (map.Cached || 0) + (map.SReclaimable || 0)
  var buffers = map.Buffers || 0
  var swapTotal = map.SwapTotal || 0
  var swapFree = map.SwapFree || 0
  var swapUsed = Math.max(0, swapTotal - swapFree)

  return {
    totalBytes: total,
    usedBytes: used,
    availBytes: avail,
    cachedBytes: cached,
    buffersBytes: buffers,
    pct: total > 0 ? clamp((used / total) * 100, 0, 100) : 0,
    swapTotalBytes: swapTotal,
    swapUsedBytes: swapUsed,
    swapPct: swapTotal > 0 ? clamp((swapUsed / swapTotal) * 100, 0, 100) : 0
  }
}

function parseProcLoadavg(raw) {
  var parts = String(raw || "").trim().split(/\s+/)
  var threads = (parts[3] || "0/0").split("/")
  return {
    load1: parseFloat(parts[0]) || 0,
    load5: parseFloat(parts[1]) || 0,
    load15: parseFloat(parts[2]) || 0,
    runningThreads: parseInt(threads[0], 10) || 0,
    totalThreads: parseInt(threads[1], 10) || 0
  }
}

function parseProcUptime(raw) {
  var secs = parseFloat(String(raw || "").trim().split(/\s+/)[0]) || 0
  var days = Math.floor(secs / 86400)
  var hours = Math.floor((secs % 86400) / 3600)
  var mins = Math.floor((secs % 3600) / 60)
  var formatted = ""
  if (days > 0) formatted += days + "d "
  if (hours > 0 || days > 0) formatted += hours + "h "
  formatted += mins + "m"
  return {
    seconds: secs,
    formatted: formatted.trim()
  }
}

function parseProcNetDev(raw, prevState, elapsedSec) {
  var lines = String(raw || "").split("\n")
  var nextState = {}
  var totalRx = 0
  var totalTx = 0
  var primaryIface = ""
  var bestTraffic = -1

  for (var i = 2; i < lines.length; i++) {
    var line = lines[i].trim()
    if (!line) continue
    var colonIdx = line.indexOf(":")
    if (colonIdx < 0) continue
    var iface = line.slice(0, colonIdx).trim()
    if (iface === "lo" || /^veth|^docker|^br-|^virbr/.test(iface)) continue

    var fields = line.slice(colonIdx + 1).trim().split(/\s+/)
    var rx = parseFloat(fields[0]) || 0
    var tx = parseFloat(fields[8]) || 0
    nextState[iface] = { rx: rx, tx: tx }
    totalRx += rx
    totalTx += tx

    if (rx + tx > bestTraffic) {
      bestTraffic = rx + tx
      primaryIface = iface
    }
  }

  var rxRate = 0
  var txRate = 0
  if (prevState && elapsedSec > 0) {
    var dRx = Math.max(0, totalRx - (prevState.totalRx || 0))
    var dTx = Math.max(0, totalTx - (prevState.totalTx || 0))
    rxRate = dRx / elapsedSec
    txRate = dTx / elapsedSec
  }

  nextState.totalRx = totalRx
  nextState.totalTx = totalTx

  return {
    iface: primaryIface || "net",
    totalRxBytes: totalRx,
    totalTxBytes: totalTx,
    rxRateBps: rxRate,
    txRateBps: txRate,
    rawState: nextState
  }
}

function parseProcDiskstats(raw, prevState, elapsedSec) {
  var lines = String(raw || "").split("\n")
  var totalReadBytes = 0
  var totalWriteBytes = 0

  for (var i = 0; i < lines.length; i++) {
    var parts = lines[i].trim().split(/\s+/)
    if (parts.length < 14) continue
    var dev = parts[2]
    // Count only whole physical drives (sda, nvme0n1, vda, mmcblk0)
    if (/^(sd[a-z]|nvme\d+n\d+|vd[a-z]|mmcblk\d+)$/.test(dev)) {
      var sectorsRead = parseFloat(parts[5]) || 0
      var sectorsWritten = parseFloat(parts[9]) || 0
      totalReadBytes += sectorsRead * 512
      totalWriteBytes += sectorsWritten * 512
    }
  }

  var readRate = 0
  var writeRate = 0
  if (prevState && elapsedSec > 0) {
    readRate = Math.max(0, totalReadBytes - (prevState.totalReadBytes || 0)) / elapsedSec
    writeRate = Math.max(0, totalWriteBytes - (prevState.totalWriteBytes || 0)) / elapsedSec
  }

  return {
    totalReadBytes: totalReadBytes,
    totalWriteBytes: totalWriteBytes,
    readRateBps: readRate,
    writeRateBps: writeRate,
    rawState: {
      totalReadBytes: totalReadBytes,
      totalWriteBytes: totalWriteBytes
    }
  }
}

function parseDetailsOutput(raw) {
  var lines = String(raw || "").split("\n")
  var details = {
    cpuModel: "Processor",
    kernel: "Linux",
    gpuCurMhz: 0,
    gpuMaxMhz: 0,
    gpuBoostMhz: 0,
    pkgTempC: 0,
    coreFreqs: {},
    avgFreqMhz: 0,
    sensors: [],
    disks: [],
    topCpuProcs: [],
    topMemProcs: []
  }

  var freqSum = 0
  var freqCount = 0

  for (var i = 0; i < lines.length; i++) {
    var line = lines[i]
    if (!line) continue
    var p = line.split("\t")
    var tag = p[0]

    if (tag === "META") {
      details.cpuModel = neutralizeMarkup(p[1] || "Processor", 64)
      details.kernel = neutralizeMarkup(p[2] || "Linux", 40)
      details.gpuCurMhz = parseInt(p[3], 10) || 0
      details.gpuMaxMhz = parseInt(p[4], 10) || 0
      details.gpuBoostMhz = parseInt(p[5], 10) || 0
      details.pkgTempC = parseInt(p[6], 10) || 0
    } else if (tag === "FREQ") {
      var cIdx = parseInt(p[1], 10) || 0
      var mhz = parseInt(p[2], 10) || 0
      details.coreFreqs[cIdx] = mhz
      if (mhz > 0) {
        freqSum += mhz
        freqCount++
      }
    } else if (tag === "SENSOR") {
      details.sensors.push({
        chip: neutralizeMarkup(p[1] || "", 28),
        label: neutralizeMarkup(p[2] || "", 32),
        value: neutralizeMarkup(p[3] || "", 32),
        unit: neutralizeMarkup(p[4] || "", 8),
        numValue: parseFloat(p[3]) || 0
      })
    } else if (tag === "DISK") {
      var totalB = parseFloat(p[4]) || 0
      var usedB = parseFloat(p[5]) || 0
      var availB = parseFloat(p[6]) || 0
      var pct = clamp(parseFloat(p[7]) || 0, 0, 100)
      details.disks.push({
        mount: neutralizeMarkup(p[1] || "/", 36),
        fstype: neutralizeMarkup(p[2] || "fs", 14),
        device: neutralizeMarkup(p[3] || "", 36),
        totalBytes: totalB,
        usedBytes: usedB,
        availBytes: availB,
        pct: pct
      })
    } else if (tag === "PROC_CPU" || tag === "PROC_MEM") {
      var pid = parseInt(p[1], 10) || 0
      if (pid <= 0) continue
      var procObj = {
        pid: pid,
        cpu: clamp(parseFloat(p[2]) || 0, 0, 999),
        mem: clamp(parseFloat(p[3]) || 0, 0, 100),
        rssBytes: (parseFloat(p[4]) || 0) * 1024,
        state: neutralizeMarkup(p[5] || "S", 4),
        name: neutralizeMarkup(p[6] || "process", 34)
      }
      if (tag === "PROC_CPU") details.topCpuProcs.push(procObj)
      else details.topMemProcs.push(procObj)
    }
  }

  if (freqCount > 0) {
    details.avgFreqMhz = Math.round(freqSum / freqCount)
  }

  return details
}

function formatBytes(bytes, decimals, formatNumberFn) {
  var b = Math.max(0, Number(bytes) || 0)
  var fmt = typeof formatNumberFn === "function"
    ? formatNumberFn
    : function(v, d) { return Number(v).toFixed(d || 0) }

  if (b < 1024) return fmt(Math.round(b), 0) + " B"
  var kb = b / 1024
  if (kb < 1024) return fmt(kb, decimals !== undefined ? decimals : 0) + " KB"
  var mb = kb / 1024
  if (mb < 1024) return fmt(mb, decimals !== undefined ? decimals : 0) + " MB"
  var gb = mb / 1024
  if (gb < 1024) return fmt(gb, decimals !== undefined ? decimals : 1) + " GB"
  var tb = gb / 1024
  return fmt(tb, 2) + " TB"
}

function formatShortBytes(bytes, formatNumberFn) {
  var b = Math.max(0, Number(bytes) || 0)
  var fmt = typeof formatNumberFn === "function"
    ? formatNumberFn
    : function(v, d) { return Number(v).toFixed(d || 0) }
  var gb = b / (1024 * 1024 * 1024)
  if (gb >= 1) return fmt(gb, 1) + "G"
  var mb = b / (1024 * 1024)
  return fmt(Math.round(mb), 0) + "M"
}

function formatRate(bytesPerSec, formatNumberFn) {
  var r = Math.max(0, Number(bytesPerSec) || 0)
  var fmt = typeof formatNumberFn === "function"
    ? formatNumberFn
    : function(v, d) { return Number(v).toFixed(d || 0) }

  if (r < 1024) return fmt(Math.round(r), 0) + " B/s"
  var kb = r / 1024
  if (kb < 1024) return fmt(Math.round(kb), 0) + " KB/s"
  var mb = kb / 1024
  if (mb < 1024) return fmt(mb, 1) + " MB/s"
  var gb = mb / 1024
  return fmt(gb, 2) + " GB/s"
}

function formatShortRate(bytesPerSec, formatNumberFn) {
  var r = Math.max(0, Number(bytesPerSec) || 0)
  var fmt = typeof formatNumberFn === "function"
    ? formatNumberFn
    : function(v, d) { return Number(v).toFixed(d || 0) }

  var kb = r / 1024
  if (kb < 1) return "0K/s"
  if (kb < 1024) return fmt(Math.round(kb), 0) + "K/s"
  var mb = kb / 1024
  return fmt(mb, 1) + "M/s"
}

function formatSpeed(bps) {
  if (bps === undefined || bps === null || isNaN(bps) || bps <= 0) return "0 B/s"
  var units = ["B/s", "KB/s", "MB/s", "GB/s"]
  var i = 0
  var val = Number(bps)
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024
    i++
  }
  return (i === 0 ? Math.round(val) : val.toFixed(1)) + " " + units[i]
}

function formatDuration(seconds) {
  if (seconds === undefined || seconds === null || isNaN(seconds) || seconds <= 0) return "0s"
  var s = Math.floor(seconds % 60)
  var m = Math.floor((seconds / 60) % 60)
  var h = Math.floor(seconds / 3600)
  if (h > 0) return h + "h " + (m < 10 ? "0" : "") + m + "m"
  if (m > 0) return m + "m " + (s < 10 ? "0" : "") + s + "s"
  return s + "s"
}

// Translate through the plugin's catalog when a translator (Monitor.t) is
// given; the English fallback otherwise.
function translate(tr, key, fallback) {
  var value = typeof tr === "function" ? tr(key, fallback) : fallback
  return (typeof value === "string" && value !== "" && value !== key) ? value : fallback
}

function toolIcon(tool) {
  if (!tool) return "\uf021"
  var t = String(tool).toLowerCase()
  if (t.indexOf("idevicebackup2") === 0) return "\uf10b"
  if (t.indexOf("rsync") >= 0) return "\uf021"
  if (t.indexOf("rclone") >= 0) return "\uf0c2"
  if (t.indexOf("curl") >= 0 || t.indexOf("wget") >= 0 || t.indexOf("aria") >= 0 || t.indexOf("dlp") >= 0) return "\uf019"
  if (t.indexOf("git") >= 0) return "\uf126"
  if (t.indexOf("cp") >= 0 || t.indexOf("mv") >= 0) return "\uf0c5"
  if (t.indexOf("dd") >= 0) return "\uf0a0"
  if (t.indexOf("pacman") >= 0 || t.indexOf("yay") >= 0 || t.indexOf("paru") >= 0) return "\uf187"
  if (t.indexOf("tar") >= 0 || t.indexOf("zstd") >= 0) return "\uf1c6"
  return "\uf021"
}

function toolDisplayTitle(tool, tr) {
  if (!tool) return translate(tr, "transfers.tool.generic", "Transfer")
  var t = String(tool).toLowerCase()
  // collect-transfers.py reports "idevicebackup2 <command>" (backup, restore, ...).
  if (t.indexOf("idevicebackup2") === 0) {
    var sub = t.split(/\s+/)[1] || "backup"
    if (sub === "backup") return translate(tr, "transfers.tool.iosBackup", "iPhone/iPad backup")
    if (sub === "restore") return translate(tr, "transfers.tool.iosRestore", "iPhone/iPad restore")
    return translate(tr, "transfers.tool.ios", "iPhone/iPad (%1)").replace("%1", neutralizeMarkup(sub, 16))
  }
  if (t.indexOf("rsync") >= 0) return "rsync"
  if (t.indexOf("rclone") >= 0) return "rclone"
  if (t.indexOf("curl") >= 0) return "curl"
  if (t.indexOf("wget") >= 0) return "wget"
  if (t.indexOf("git") >= 0) return "git"
  if (t.indexOf("cp") >= 0) return "cp"
  if (t.indexOf("mv") >= 0) return "mv"
  if (t.indexOf("tar") === 0) return "tar"
  if (t.indexOf("dd") >= 0) return "dd"
  return tool
}

// One line for a finished transfer: "rsync (1.2 GB in 3m 05s)". The size or the
// time can be missing (the collector only knows what it saw).
function formatRecentTransfer(item, tr) {
  if (!item) return ""
  var title = toolDisplayTitle(item.tool, tr)
  var size = Number(item.total_bytes) > 0 ? formatBytes(item.total_bytes, 1) : ""
  var elapsed = item.elapsed_str ? String(item.elapsed_str) : ""
  if (size && elapsed) return translate(tr, "transfers.recentLine", "%1 (%2 in %3)").replace("%1", title).replace("%2", size).replace("%3", elapsed)
  if (size || elapsed) return title + " (" + (size || elapsed) + ")"
  return title
}

// The folder a Disks-tab entry opens. collect-details.sh merges Btrfs
// subvolumes on one device into labels like "/ · /home" (prefer /home there).
// Anything that is not an absolute path (an iPhone/iPad entry, "iPhone · Name")
// has no folder to open: "".
function diskOpenPath(mount) {
  if (!mount) return ""
  var target = String(mount).trim()
  if (target.indexOf("·") >= 0 || target.indexOf("•") >= 0) {
    var parts = target.split(/[·•]/).map(function(s) { return s.trim() })
    target = parts.indexOf("/home") >= 0 ? "/home" : parts[0]
  }
  return target.charAt(0) === "/" ? target : ""
}

function shortenPath(p, maxLen) {
  if (!p) return ""
  var s = String(p)
  if (!maxLen) maxLen = 38
  if (s.length <= maxLen) return s
  var parts = s.split("/").filter(function(x) { return x.length > 0 })
  if (parts.length <= 2) {
    return s.substring(0, maxLen - 3) + "…"
  }
  // "~/a/b" (see tildePath) keeps its "~"; anything else keeps the leading "/".
  var lead = parts[0] === "~" ? "" : "/"
  var first = parts[0]
  var last = parts[parts.length - 1]
  var prev = parts[parts.length - 2]
  var combined = lead + first + "/…/" + prev + "/" + last
  if (combined.length <= maxLen) return combined
  return lead + first + "/…/" + last
}

// "/home/me/x" as "~/x": the home folder is noise in a path the user reads.
function tildePath(p, home) {
  if (!p) return ""
  var s = String(p)
  var h = home ? String(home).replace(/\/+$/, "") : ""
  if (!h || h === "/") return s
  if (s === h) return "~"
  return s.indexOf(h + "/") === 0 ? "~" + s.substring(h.length) : s
}

// "↑ " for a transfer leaving this computer, "↓ " for one arriving, "" for local.
function directionArrow(direction) {
  return direction === "upload" ? "↑ " : direction === "download" ? "↓ " : ""
}

// The path of a transfer as the panel shows it: whole when it fits in maxLen.
function transferPath(p, home, maxLen) {
  return shortenPath(tildePath(p, home), maxLen || 38)
}

function parseJsonSafely(str, fallback) {
  if (!str || typeof str !== "string") return fallback
  try {
    return JSON.parse(str)
  } catch (e) {
    return fallback
  }
}

// Lets the node tests (dev/tests/) load this file as is; QML ignores it.
if (typeof module !== "undefined" && module.exports) {
  module.exports = {
    MAX_HISTORY: MAX_HISTORY, neutralizeMarkup: neutralizeMarkup, clamp: clamp, pushHistory: pushHistory,
    parseProcStat: parseProcStat, parseProcMeminfo: parseProcMeminfo, parseProcLoadavg: parseProcLoadavg,
    parseProcUptime: parseProcUptime, parseProcNetDev: parseProcNetDev, parseProcDiskstats: parseProcDiskstats,
    parseDetailsOutput: parseDetailsOutput, formatBytes: formatBytes, formatShortBytes: formatShortBytes,
    formatRate: formatRate, formatShortRate: formatShortRate, formatSpeed: formatSpeed,
    formatDuration: formatDuration, translate: translate, toolIcon: toolIcon, toolDisplayTitle: toolDisplayTitle,
    formatRecentTransfer: formatRecentTransfer, diskOpenPath: diskOpenPath, shortenPath: shortenPath, tildePath: tildePath, transferPath: transferPath, directionArrow: directionArrow,
    parseJsonSafely: parseJsonSafely
  }
}
