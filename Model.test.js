import { describe, it, expect } from 'vitest'

// Pure JS functions extracted from Model.js for testing
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

function toolIcon(tool) {
  if (!tool) return "\uf021"
  var t = String(tool).toLowerCase()
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

function toolDisplayTitle(tool) {
  if (!tool) return "Transfer"
  var t = String(tool).toLowerCase()
  if (t.indexOf("rsync") >= 0) return "rsync"
  if (t.indexOf("rclone") >= 0) return "rclone"
  if (t.indexOf("curl") >= 0) return "curl"
  if (t.indexOf("wget") >= 0) return "wget"
  if (t.indexOf("git") >= 0) return "git"
  if (t.indexOf("cp") >= 0) return "cp"
  if (t.indexOf("mv") >= 0) return "mv"
  if (t.indexOf("dd") >= 0) return "dd"
  return tool
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
  var first = parts[0]
  var last = parts[parts.length - 1]
  var prev = parts[parts.length - 2]
  var combined = "/" + first + "/…/" + prev + "/" + last
  if (combined.length <= maxLen) return combined
  return "/" + first + "/…/" + last
}

function parseJsonSafely(str, fallback) {
  if (!str || typeof str !== "string") return fallback
  try {
    return JSON.parse(str)
  } catch (e) {
    return fallback
  }
}

describe('gusthx.resources Model.js', () => {
  describe('neutralizeMarkup', () => {
    it('strips control characters', () => {
      expect(neutralizeMarkup('test\x00\x1f')).toBe('test')
      expect(neutralizeMarkup('\x7f')).toBe('')
    })
    it('escapes HTML entities', () => {
      expect(neutralizeMarkup('a & b')).toBe('a \uFF06 b')
      expect(neutralizeMarkup('a < b')).toBe('a \uFF1C b')
      expect(neutralizeMarkup('a > b')).toBe('a \uFF1E b')
    })
    it('truncates to maxLen', () => {
      expect(neutralizeMarkup('a'.repeat(200), 100)).toHaveLength(100)
      expect(neutralizeMarkup('a'.repeat(200), 100).endsWith('…')).toBe(true)
    })
    it('uses default limit', () => {
      expect(neutralizeMarkup('a'.repeat(200))).toHaveLength(160)
    })
    it('handles empty/null', () => {
      expect(neutralizeMarkup('', 10)).toBe('')
      expect(neutralizeMarkup(null, 10)).toBe('')
      expect(neutralizeMarkup(undefined, 10)).toBe('')
    })
  })

  describe('clamp', () => {
    it('clamps within range', () => {
      expect(clamp(5, 0, 10)).toBe(5)
      expect(clamp(-5, 0, 10)).toBe(0)
      expect(clamp(15, 0, 10)).toBe(10)
    })
    it('handles non-finite', () => {
      expect(clamp(NaN, 0, 10)).toBe(0)
      expect(clamp(Infinity, 0, 10)).toBe(0)
      expect(clamp(-Infinity, 0, 10)).toBe(0)
    })
    it('handles string numbers', () => {
      expect(clamp('5', 0, 10)).toBe(5)
      expect(clamp('abc', 0, 10)).toBe(0)
    })
  })

  describe('pushHistory', () => {
    it('pushes and maintains limit', () => {
      let arr = []
      arr = pushHistory(arr, 1, 3)
      arr = pushHistory(arr, 2, 3)
      arr = pushHistory(arr, 3, 3)
      expect(arr).toEqual([1, 2, 3])
      arr = pushHistory(arr, 4, 3)
      expect(arr).toEqual([2, 3, 4])
    })
    it('clamps values', () => {
      let arr = []
      arr = pushHistory(arr, -10, 5)
      arr = pushHistory(arr, 1e20, 5)
      expect(arr).toEqual([0, 1e15])
    })
    it('uses default MAX_HISTORY', () => {
      let arr = []
      for (let i = 0; i < 70; i++) arr = pushHistory(arr, i)
      expect(arr.length).toBe(60)
      expect(arr[0]).toBe(10)
    })
    it('handles non-array input', () => {
      expect(pushHistory(null, 1, 5)).toEqual([1])
      expect(pushHistory(undefined, 1, 5)).toEqual([1])
    })
  })

  describe('parseProcStat', () => {
    it('parses basic cpu line', () => {
      const raw = 'cpu  1000 200 300 5000 100 50 25 10 0'
      const result = parseProcStat(raw, null, 1)
      expect(result.rawState.total).toEqual({
        user: 1200,
        sys: 375,
        iowait: 100,
        idle: 5100,
        total: 6685
      })
    })
    it('parses per-core lines', () => {
      const raw = 'cpu  1000 200 300 5000 100 50 25 10 0\ncpu0  500 100 150 2500 50 25 12 5 0\ncpu1  500 100 150 2500 50 25 12 5 0'
      const result = parseProcStat(raw, null, 1)
      expect(result.cores).toHaveLength(2)
      expect(result.cores[0].id).toBe(0)
      expect(result.cores[1].id).toBe(1)
    })
    it('calculates percentages with prevState', () => {
      const prev = {
        total: { user: 1000, sys: 300, iowait: 50, idle: 4000, total: 5350 },
        cores: {
          cpu0: { user: 500, sys: 150, iowait: 25, idle: 2000, total: 2675 },
          cpu1: { user: 500, sys: 150, iowait: 25, idle: 2000, total: 2675 }
        },
        ctxt: 1000
      }
      const raw = 'cpu  2000 400 600 8000 200 100 50 20 0\ncpu0  1000 200 300 4000 100 50 25 10 0\ncpu1  1000 200 300 4000 100 50 25 10 0'
      const result = parseProcStat(raw, prev, 1)
      // dt = 6020, didle = 4150, cpuPct = (6020-4150)/6020*100 = 31.06%
      expect(result.cpuPct).toBeGreaterThan(30)
      expect(result.cpuPct).toBeLessThan(32)
    })
    it('parses procs_running and procs_blocked', () => {
      const raw = 'cpu  1000 200 300 5000 100 50 25 10 0\nprocs_running 5\nprocs_blocked 2\nctxt 5000'
      const result = parseProcStat(raw, null, 1)
      expect(result.procsRunning).toBe(5)
      expect(result.procsBlocked).toBe(2)
      expect(result.rawState.ctxt).toBe(5000)
    })
    it('calculates ctxtRate', () => {
      const prev = { ctxt: 1000 }
      const raw = 'cpu  1000 200 300 5000 100 50 25 10 0\nctxt 2000'
      const result = parseProcStat(raw, prev, 2)
      expect(result.ctxtRate).toBe(500)
    })
    it('handles missing prevState', () => {
      const result = parseProcStat('cpu  1000 200 300 5000 100 50 25 10 0', null, 1)
      expect(result.cpuPct).toBe(0)
      expect(result.cores).toHaveLength(0)
    })
  })

  describe('parseProcMeminfo', () => {
    it('parses standard fields', () => {
      const raw = `MemTotal:       16384000 kB
MemFree:        4096000 kB
MemAvailable:   8192000 kB
Buffers:        1024000 kB
Cached:         3072000 kB
SReclaimable:   512000 kB
SwapTotal:      8388608 kB
SwapFree:       4194304 kB`
      const result = parseProcMeminfo(raw)
      expect(result.totalBytes).toBe(16384000 * 1024)
      expect(result.availBytes).toBe(8192000 * 1024)
      expect(result.usedBytes).toBe((16384000 - 8192000) * 1024)
      expect(result.cachedBytes).toBe((3072000 + 512000) * 1024)
      expect(result.buffersBytes).toBe(1024000 * 1024)
      expect(result.swapTotalBytes).toBe(8388608 * 1024)
      expect(result.swapUsedBytes).toBe((8388608 - 4194304) * 1024)
      expect(result.pct).toBe(50) // used/total = 50%
      expect(result.swapPct).toBe(50)
    })
    it('falls back to MemFree when MemAvailable missing', () => {
      const raw = `MemTotal:       16384000 kB
MemFree:        4096000 kB`
      const result = parseProcMeminfo(raw)
      expect(result.availBytes).toBe(4096000 * 1024)
      expect(result.usedBytes).toBe((16384000 - 4096000) * 1024)
    })
    it('handles empty/missing', () => {
      expect(parseProcMeminfo('')).toEqual({
        totalBytes: 0, usedBytes: 0, availBytes: 0, cachedBytes: 0,
        buffersBytes: 0, pct: 0, swapTotalBytes: 0, swapUsedBytes: 0, swapPct: 0
      })
    })
  })

  describe('parseProcLoadavg', () => {
    it('parses load averages', () => {
      const raw = '0.52 0.58 0.59 2/400 12345'
      const result = parseProcLoadavg(raw)
      expect(result.load1).toBe(0.52)
      expect(result.load5).toBe(0.58)
      expect(result.load15).toBe(0.59)
      expect(result.runningThreads).toBe(2)
      expect(result.totalThreads).toBe(400)
    })
    it('handles missing threads', () => {
      const raw = '1.0 2.0 3.0'
      const result = parseProcLoadavg(raw)
      expect(result.runningThreads).toBe(0)
      expect(result.totalThreads).toBe(0)
    })
    it('handles empty', () => {
      expect(parseProcLoadavg('')).toEqual({
        load1: 0, load5: 0, load15: 0, runningThreads: 0, totalThreads: 0
      })
    })
  })

  describe('parseProcUptime', () => {
    it('formats days, hours, minutes', () => {
      expect(parseProcUptime('90061').formatted).toBe('1d 1h 1m') // 1 day + 1 hour + 1 min
      expect(parseProcUptime('3661').formatted).toBe('1h 1m')
      expect(parseProcUptime('120').formatted).toBe('2m')
      expect(parseProcUptime('59').formatted).toBe('0m')
    })
    it('returns seconds', () => {
      expect(parseProcUptime('123.45').seconds).toBe(123.45)
    })
    it('handles empty', () => {
      expect(parseProcUptime('').seconds).toBe(0)
      expect(parseProcUptime('').formatted).toBe('0m')
    })
  })

  describe('parseProcNetDev', () => {
    it('parses interface stats', () => {
      const raw = `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
  eth0: 1000000 1000 0 0 0 0 0 0 2000000 2000 0 0 0 0 0 0
  wlan0: 500000 500 0 0 0 0 0 0 3000000 3000 0 0 0 0 0 0
  lo: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0`
      const result = parseProcNetDev(raw, null, 1)
      expect(result.totalRxBytes).toBe(1500000)
      expect(result.totalTxBytes).toBe(5000000)
      expect(result.iface).toBe('wlan0') // higher traffic
      expect(result.rawState.eth0).toEqual({ rx: 1000000, tx: 2000000 })
      expect(result.rawState.wlan0).toEqual({ rx: 500000, tx: 3000000 })
      expect(result.rawState.lo).toBeUndefined() // skipped
    })
    it('skips virtual interfaces', () => {
      const raw = `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
  veth0: 1000 10 0 0 0 0 0 0 1000 10 0 0 0 0 0 0
  docker0: 2000 20 0 0 0 0 0 0 2000 20 0 0 0 0 0 0
  br-abc: 3000 30 0 0 0 0 0 0 3000 30 0 0 0 0 0 0`
      const result = parseProcNetDev(raw, null, 1)
      expect(result.totalRxBytes).toBe(0)
      expect(result.totalTxBytes).toBe(0)
      expect(result.iface).toBe('net')
    })
    it('calculates rates with prevState', () => {
      const prev = { totalRx: 1000000, totalTx: 2000000 }
      const raw = `Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
  eth0: 2000000 1000 0 0 0 0 0 0 3000000 2000 0 0 0 0 0 0`
      const result = parseProcNetDev(raw, prev, 2)
      expect(result.rxRateBps).toBe(500000) // (2000000 - 1000000) / 2
      expect(result.txRateBps).toBe(500000) // (3000000 - 2000000) / 2
    })
    it('handles missing prevState', () => {
      const result = parseProcNetDev('eth0: 1000 0 0 0 0 0 0 0 2000 0 0 0 0 0 0', null, 1)
      expect(result.rxRateBps).toBe(0)
      expect(result.txRateBps).toBe(0)
    })
  })

  describe('parseProcDiskstats', () => {
    it('parses physical drives only', () => {
      const raw = `0 0 sda 1000 2000 3000 4000 5000 6000 7000 8000 9000 10000 11000 12000
  0 0 sda1 100 200 300 400 500 600 700 800 900 1000 1100 1200 1300 1400
  0 0 nvme0n1 500 1000 1500 2000 2500 3000 3500 4000 4500 5000 5500 6000
  0 0 loop0 10 20 30 40 50 60 70 80 90 100 110 120`
      const result = parseProcDiskstats(raw, null, 1)
      // sda: sectorsRead=3000, sectorsWritten=7000
      // nvme0n1: sectorsRead=1500, sectorsWritten=3500
      // Total read = (3000 + 1500) * 512 = 2304000
      // Total write = (7000 + 3500) * 512 = 5376000
      expect(result.totalReadBytes).toBe(2304000)
      expect(result.totalWriteBytes).toBe(5376000)
    })
    it('calculates rates', () => {
      const prev = { totalReadBytes: 1000000, totalWriteBytes: 2000000 }
      const raw = `0 0 sda 3000 4000 5000 6000 7000 8000 9000 10000 11000 12000 13000 14000`
      const result = parseProcDiskstats(raw, prev, 2)
      // read = 5000 * 512 = 2560000, diff = 1560000, rate = 780000
      // write = 9000 * 512 = 4608000, diff = 2608000, rate = 1304000
      expect(result.readRateBps).toBe(780000)
      expect(result.writeRateBps).toBe(1304000)
    })
    it('handles empty', () => {
      expect(parseProcDiskstats('')).toEqual({
        totalReadBytes: 0, totalWriteBytes: 0, readRateBps: 0, writeRateBps: 0,
        rawState: { totalReadBytes: 0, totalWriteBytes: 0 }
      })
    })
  })

  describe('parseDetailsOutput', () => {
    it('parses META section', () => {
      const raw = 'META\tIntel Core i7\t5.10.0\t1500\t2000\t1800\t45'
      const result = parseDetailsOutput(raw)
      expect(result.cpuModel).toBe('Intel Core i7')
      expect(result.kernel).toBe('5.10.0')
      expect(result.gpuCurMhz).toBe(1500)
      expect(result.gpuMaxMhz).toBe(2000)
      expect(result.gpuBoostMhz).toBe(1800)
      expect(result.pkgTempC).toBe(45)
    })
    it('parses FREQ section', () => {
      const raw = 'FREQ\t0\t2400\nFREQ\t1\t2500\nFREQ\t2\t0'
      const result = parseDetailsOutput(raw)
      expect(result.coreFreqs[0]).toBe(2400)
      expect(result.coreFreqs[1]).toBe(2500)
      expect(result.coreFreqs[2]).toBe(0)
      expect(result.avgFreqMhz).toBe(2450)
    })
    it('parses SENSOR section', () => {
      const raw = 'SENSOR\tcoretemp\tPackage id 0\t45.0\t°C'
      const result = parseDetailsOutput(raw)
      expect(result.sensors).toHaveLength(1)
      expect(result.sensors[0]).toEqual({
        chip: 'coretemp',
        label: 'Package id 0',
        value: '45.0',
        unit: '°C',
        numValue: 45.0
      })
    })
    it('parses DISK section', () => {
      const raw = 'DISK\t/\text4\t/dev/sda2\t1000000000\t500000000\t500000000\t50'
      const result = parseDetailsOutput(raw)
      expect(result.disks).toHaveLength(1)
      expect(result.disks[0]).toEqual({
        mount: '/',
        fstype: 'ext4',
        device: '/dev/sda2',
        totalBytes: 1000000000,
        usedBytes: 500000000,
        availBytes: 500000000,
        pct: 50
      })
    })
    it('parses PROC_CPU and PROC_MEM', () => {
      const raw = 'PROC_CPU\t1234\t50.5\t25.3\t102400\tS\tfirefox\nPROC_MEM\t5678\t10.2\t40.1\t204800\tR\tchrome'
      const result = parseDetailsOutput(raw)
      expect(result.topCpuProcs).toHaveLength(1)
      expect(result.topCpuProcs[0]).toEqual({
        pid: 1234,
        cpu: 50.5,
        mem: 25.3,
        rssBytes: 102400 * 1024,
        state: 'S',
        name: 'firefox'
      })
      expect(result.topMemProcs).toHaveLength(1)
      expect(result.topMemProcs[0].pid).toBe(5678)
    })
    it('clamps values', () => {
      const raw = 'PROC_CPU\t1234\t1500\t150\t204800\tS\ttest'
      const result = parseDetailsOutput(raw)
      expect(result.topCpuProcs[0].cpu).toBe(999)
      expect(result.topCpuProcs[0].mem).toBe(100)
    })
    it('sanitizes strings', () => {
      const raw = 'META\t<Processor>&\t5.10.0\t0\t0\t0\t0'
      const result = parseDetailsOutput(raw)
      expect(result.cpuModel).toContain('\uFF1C')
      expect(result.cpuModel).toContain('\uFF06')
    })
  })

  describe('formatBytes', () => {
    it('formats bytes', () => {
      expect(formatBytes(500)).toBe('500 B')
      expect(formatBytes(1023)).toBe('1023 B')
      expect(formatBytes(1024)).toBe('1 KB')
      expect(formatBytes(1536)).toBe('2 KB') // rounds
      expect(formatBytes(1024 * 1024)).toBe('1 MB')
      expect(formatBytes(1024 * 1024 * 1024)).toBe('1.0 GB')
      expect(formatBytes(1024 * 1024 * 1024 * 1024)).toBe('1.00 TB')
    })
    it('handles custom decimals', () => {
      expect(formatBytes(1024 * 1024 * 1.5, 2)).toBe('1.50 MB')
    })
    it('handles custom formatter', () => {
      const fmt = (v, d) => d === undefined ? String(v) : v.toFixed(d).replace('.', ',')
      expect(formatBytes(1024 * 1024, 2, fmt)).toBe('1,00 MB')
    })
    it('handles zero/negative', () => {
      expect(formatBytes(0)).toBe('0 B')
      expect(formatBytes(-100)).toBe('0 B')
    })
  })

  describe('formatShortBytes', () => {
    it('formats in short notation', () => {
      expect(formatShortBytes(500 * 1024 * 1024)).toBe('500M')
      expect(formatShortBytes(1.5 * 1024 * 1024 * 1024)).toBe('1.5G')
      expect(formatShortBytes(2 * 1024 * 1024 * 1024)).toBe('2.0G')
    })
  })

  describe('formatRate', () => {
    it('formats bytes/sec', () => {
      expect(formatRate(500)).toBe('500 B/s')
      expect(formatRate(1024)).toBe('1 KB/s')
      expect(formatRate(1024 * 1024)).toBe('1.0 MB/s')
      expect(formatRate(1024 * 1024 * 1024)).toBe('1.00 GB/s')
    })
    it('handles custom formatter', () => {
      const fmt = (v, d) => d === undefined ? String(v) : v.toFixed(d).replace('.', ',')
      expect(formatRate(1024 * 1024, fmt)).toBe('1,0 MB/s')
    })
  })

  describe('formatShortRate', () => {
    it('formats short rate', () => {
      expect(formatShortRate(500)).toBe('0K/s')
      expect(formatShortRate(1024)).toBe('1K/s')
      expect(formatShortRate(1024 * 1024)).toBe('1.0M/s')
      expect(formatShortRate(1024 * 1024 * 1024)).toBe('1024.0M/s')
    })
  })

  describe('formatSpeed', () => {
    it('handles zero and negative', () => {
      expect(formatSpeed(0)).toBe('0 B/s')
      expect(formatSpeed(-100)).toBe('0 B/s')
    })
    it('formats speeds correctly', () => {
      expect(formatSpeed(1024)).toBe('1.0 KB/s')
      expect(formatSpeed(1024 * 1024 * 5.5)).toBe('5.5 MB/s')
    })
  })

  describe('formatDuration', () => {
    it('handles zero and negative', () => {
      expect(formatDuration(0)).toBe('0s')
      expect(formatDuration(-5)).toBe('0s')
    })
    it('formats seconds, minutes, hours', () => {
      expect(formatDuration(45)).toBe('45s')
      expect(formatDuration(125)).toBe('2m 05s')
      expect(formatDuration(3665)).toBe('1h 01m')
    })
  })

  describe('toolIcon', () => {
    it('returns correct icons', () => {
      expect(toolIcon('rsync')).toBe('\uf021')
      expect(toolIcon('rclone')).toBe('\uf0c2')
      expect(toolIcon('curl')).toBe('\uf019')
      expect(toolIcon('git')).toBe('\uf126')
      expect(toolIcon('cp')).toBe('\uf0c5')
      expect(toolIcon('dd')).toBe('\uf0a0')
    })
  })

  describe('shortenPath', () => {
    it('handles empty and short paths', () => {
      expect(shortenPath('')).toBe('')
      expect(shortenPath('/home/gusthx/file.txt')).toBe('/home/gusthx/file.txt')
    })
  })

  describe('parseJsonSafely', () => {
    it('parses JSON safely', () => {
      expect(parseJsonSafely('{"a":1}', null)).toEqual({ a: 1 })
      expect(parseJsonSafely('bad json', { def: 1 })).toEqual({ def: 1 })
    })
  })
})