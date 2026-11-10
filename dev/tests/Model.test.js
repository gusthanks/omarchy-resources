import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

// Run with `node --test dev/tests/` (or dev/run.sh test): no npm packages.
// The few vitest-style matchers used here, on top of node:assert.
function expect(actual, message) {
  return {
    toBe: (expected) => assert.ok(Object.is(actual, expected), message || `expected ${JSON.stringify(actual)} to be ${JSON.stringify(expected)}`),
    toEqual: (expected) => assert.deepStrictEqual(actual, expected, message),
    toContain: (item) => assert.ok(actual.includes(item), message || `expected ${JSON.stringify(actual)} to contain ${JSON.stringify(item)}`),
    toHaveLength: (n) => assert.equal(actual.length, n, message),
    toBeGreaterThan: (n) => assert.ok(actual > n, message || `expected ${actual} > ${n}`),
    toBeLessThan: (n) => assert.ok(actual < n, message || `expected ${actual} < ${n}`),
    toBeGreaterThanOrEqual: (n) => assert.ok(actual >= n, message || `expected ${actual} >= ${n}`),
    toBeLessThanOrEqual: (n) => assert.ok(actual <= n, message || `expected ${actual} <= ${n}`),
    toBeCloseTo: (n, digits = 2) => assert.ok(Math.abs(actual - n) < Math.pow(10, -digits) / 2, message || `expected ${actual} to be close to ${n}`),
    toBeNull: () => assert.equal(actual, null, message),
    toBeUndefined: () => assert.equal(actual, undefined, message),
    toBeTruthy: () => assert.ok(actual, message),
    toBeFalsy: () => assert.ok(!actual, message)
  }
}

// Tests run against the real Model.js (a QML JavaScript library that also
// exports through `module.exports` when one exists; its `.pragma library`
// line is QML-only) and the real i18n/ catalogs, so nothing here is a copy
// that can drift from the plugin.
const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
const mod = { exports: {} }
new Function('module', readFileSync(join(root, 'Model.js'), 'utf8').replace(/^\.pragma library\s*$/m, ''))(mod)
const {
  MAX_HISTORY, neutralizeMarkup, clamp, pushHistory, parseProcStat, parseProcMeminfo,
  parseProcLoadavg, parseProcUptime, parseProcNetDev, parseProcDiskstats, parseDetailsOutput,
  formatBytes, formatShortBytes, formatRate, formatShortRate, formatSpeed, formatDuration,
  toolIcon, toolDisplayTitle, formatRecentTransfer, diskOpenPath, shortenPath, tildePath, transferPath, directionArrow, parseJsonSafely,
  formatDataSize, formatDataRate, fillArgs, transferKind, isCheckKind, kindLabel, sourceSummary, describeOverall, describeScan,
  describePlan, describeBatch, recentIcon, notificationFor
} = mod.exports

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
      expect(shortenPath('/home/user/file.txt')).toBe('/home/user/file.txt')
    })
    it('keeps the root and the last folders of a long path', () => {
      expect(shortenPath('/mnt/disk/a/b/c/photos/img.tar', 24)).toBe('/mnt/…/photos/img.tar')
      expect(shortenPath('~/a/b/c/d/photos/img.tar', 20)).toBe('~/…/photos/img.tar')
    })
  })

  describe('tildePath and transferPath', () => {
    it('shows the home folder as ~', () => {
      expect(tildePath('/home/me/x/y', '/home/me')).toBe('~/x/y')
      expect(tildePath('/home/me', '/home/me/')).toBe('~')
      expect(tildePath('/home/meow/x', '/home/me')).toBe('/home/meow/x')
      expect(tildePath('/mnt/x', '')).toBe('/mnt/x')
      expect(tildePath('gdrive:b/', '/home/me')).toBe('gdrive:b/')
    })
    it('marks uploads and downloads', () => {
      expect(directionArrow('upload')).toBe('↑ ')
      expect(directionArrow('download')).toBe('↓ ')
      expect(directionArrow('')).toBe('')
      expect(directionArrow(undefined)).toBe('')
    })
    it('shortens after replacing the home folder', () => {
      expect(transferPath('/home/me/a.tar', '/home/me', 10)).toBe('~/a.tar')
      expect(transferPath('', '/home/me')).toBe('')
    })
  })

  describe('parseJsonSafely', () => {
    it('parses JSON safely', () => {
      expect(parseJsonSafely('{"a":1}', null)).toEqual({ a: 1 })
      expect(parseJsonSafely('bad json', { def: 1 })).toEqual({ def: 1 })
    })
  })
})
describe('iPhone/iPad support', () => {
  it('parses the DISK line collect-details.sh prints for a device', () => {
    const d = parseDetailsOutput('DISK\tiPhone · Test Phone\tafc\tiPhone14,2\t128000000000\t96000000000\t32000000000\t75\n')
    expect(d.disks).toHaveLength(1)
    expect(d.disks[0]).toEqual({
      mount: 'iPhone · Test Phone', fstype: 'afc', device: 'iPhone14,2',
      totalBytes: 128000000000, usedBytes: 96000000000, availBytes: 32000000000, pct: 75
    })
  })
  it('ignores the UDID column the cache file adds', () => {
    const d = parseDetailsOutput('DISK\tiPad · Tab\tafc\tiPad13,1\t64\t32\t32\t50\t00008030-000A1B2C3D4E5F60\n')
    expect(d.disks[0].mount).toBe('iPad · Tab')
    expect(Object.keys(d.disks[0]).includes('udid')).toBe(false)
  })
  it('names idevicebackup2 transfers through the catalog', () => {
    const tr = (k, f) => ({ 'transfers.tool.iosBackup': 'Backup do iPhone/iPad' })[k] || f
    expect(toolDisplayTitle('idevicebackup2 backup', tr)).toBe('Backup do iPhone/iPad')
    expect(toolDisplayTitle('idevicebackup2 backup')).toBe('iPhone/iPad backup')
    expect(toolDisplayTitle('idevicebackup2 restore')).toBe('iPhone/iPad restore')
    expect(toolDisplayTitle('idevicebackup2 info')).toBe('iPhone/iPad (info)')
    expect(toolIcon('idevicebackup2 backup')).toBe('')
  })
})

describe('diskOpenPath', () => {
  it('opens plain mount points', () => {
    expect(diskOpenPath('/')).toBe('/')
    expect(diskOpenPath('/run/media/user/USB')).toBe('/run/media/user/USB')
  })
  it('prefers /home in merged Btrfs labels', () => {
    expect(diskOpenPath('/ · /home')).toBe('/home')
    expect(diskOpenPath('/ · /var')).toBe('/')
  })
  it('has nothing to open for iPhone/iPad entries', () => {
    expect(diskOpenPath('iPhone · Test Phone')).toBe('')
    expect(diskOpenPath('')).toBe('')
  })
})

describe('formatRecentTransfer', () => {
  const tr = (k, f) => ({ 'transfers.recentLine': '%1 (%2 em %3)' })[k] || f
  it('shows size and time, translated', () => {
    // Transfers use SI units, like rsync --stats.
    expect(formatRecentTransfer({ tool: 'rsync', total_bytes: 1.5e9, elapsed_str: '3m 05s' }, tr)).toBe('rsync (1.50 GB em 3m 05s)')
    expect(formatRecentTransfer({ tool: 'rsync', total_bytes: 1.5e9, elapsed_str: '3m 05s' })).toBe('rsync (1.50 GB in 3m 05s)')
  })
  it('does not break when the size or the time is missing', () => {
    expect(formatRecentTransfer({ tool: 'cp', total_bytes: 0, elapsed_str: '20s' }, tr)).toBe('cp (20s)')
    expect(formatRecentTransfer({ tool: 'cp', total_bytes: '', elapsed_str: '' }, tr)).toBe('cp')
    expect(formatRecentTransfer(null, tr)).toBe('')
  })
})

describe('formatRecentTransfer: outcomes and kinds', () => {
  it('says how far a stopped transfer got', () => {
    expect(formatRecentTransfer({ tool: 'rsync', outcome: 'stopped', total_bytes: 7.2e9, overall_total_bytes: 20e9, elapsed_str: '5m' }))
      .toBe('rsync stopped at 7.20 GB of 20.0 GB')
  })
  it('names a finished batch', () => {
    expect(formatRecentTransfer({ tool: 'batch', kind: 'batch', name: 'nightly.sh', total_bytes: 52.3e9, elapsed_str: '1h 02m' }))
      .toBe('Batch nightly.sh (52.3 GB in 1h 02m)')
  })
  it('marks a verification', () => {
    expect(formatRecentTransfer({ tool: 'sha256sum', kind: 'verification', total_bytes: 2e9, elapsed_str: '40s' }))
      .toBe('sha256sum · Verification (2.00 GB in 40s)')
  })
  it('picks an icon by outcome', () => {
    expect(recentIcon({ outcome: 'stopped' })).toBe('')
    expect(recentIcon({ outcome: 'finished' })).toBe('')
    expect(recentIcon({})).toBe('')
  })
})

describe('SI units for transfers', () => {
  it('formats sizes in powers of 1000', () => {
    expect(formatDataSize(1e9)).toBe('1.00 GB')
    expect(formatDataSize(113e9)).toBe('113.0 GB')
    expect(formatDataSize(999)).toBe('999 B')
    expect(formatDataSize(1500)).toBe('2 kB')
    expect(formatDataSize(523e6)).toBe('523 MB')
    expect(formatDataSize(1.5e12)).toBe('1.50 TB')
    expect(formatDataSize(0)).toBe('0 B')
    expect(formatDataSize(-5)).toBe('0 B')
  })
  it('never shows 1000 of a unit', () => {
    expect(formatDataSize(999600)).toBe('1 MB')
    expect(formatDataSize(999.97e9)).toBe('1.00 TB')
  })
  it('formats rates', () => {
    expect(formatDataRate(29e6)).toBe('29.0 MB/s')
    expect(formatDataRate(500)).toBe('500 B/s')
    expect(formatDataRate(1500)).toBe('2 kB/s')
    expect(formatDataRate(2.5e9)).toBe('2.50 GB/s')
    expect(formatDataRate(0)).toBe('0 B/s')
  })
  it('uses the locale number formatter', () => {
    const fmt = (v, d) => Number(v).toFixed(d).replace('.', ',')
    expect(formatDataSize(113e9, fmt)).toBe('113,0 GB')
    expect(formatDataRate(29e6, fmt)).toBe('29,0 MB/s')
  })
})

describe('fillArgs', () => {
  it('substitutes every placeholder once', () => {
    expect(fillArgs('%1 of %2', ['a%2', 'b'])).toBe('a%2 of b')
    expect(fillArgs('%1 %3', ['x'])).toBe('x %3')
  })
})

describe('transfer kinds', () => {
  it('defaults to a transfer', () => {
    expect(transferKind({})).toBe('transfer')
    expect(transferKind(null)).toBe('transfer')
    expect(kindLabel({})).toBe('')
    expect(isCheckKind({})).toBe(false)
  })
  it('labels dry runs, listings and verifications', () => {
    expect(kindLabel({ kind: 'simulation' })).toBe('Dry run')
    expect(kindLabel({ kind: 'listing' })).toBe('Listing')
    expect(kindLabel({ kind: 'verification', verify_mode: 'check' })).toBe('Verification')
    expect(kindLabel({ kind: 'verification', verify_mode: 'hash' })).toBe('Computing checksums')
    expect(isCheckKind({ kind: 'simulation' })).toBe(true)
    expect(isCheckKind({ kind: 'verification' })).toBe(false)
    const tr = (k, f) => ({ 'transfers.kind.simulation': 'Simulação' })[k] || f
    expect(kindLabel({ kind: 'simulation' }, tr)).toBe('Simulação')
  })
  it('gives checksum tools their own icon', () => {
    expect(toolIcon('sha256sum')).toBe('')
    expect(toolIcon('b2sum')).toBe('')
    expect(toolIcon('rsync')).toBe('')
  })
})

describe('sourceSummary', () => {
  it('shows one source as is', () => {
    expect(sourceSummary({ source: '/home/me/a', sources: ['/home/me/a'], source_count: 1 }, '/home/me')).toBe('~/a')
    expect(sourceSummary({ source: '/home/me/a' }, '/home/me')).toBe('~/a')
    expect(sourceSummary({ source: '' }, '/home/me')).toBe('')
  })
  it('counts the other sources', () => {
    const sources = ['/src/hermes', '/src/b', '/src/c', '/src/d', '/src/e', '/src/f', '/src/g', '/src/h', '/src/i']
    expect(sourceSummary({ source: sources[0], sources, source_count: 9 }, '/home/me')).toBe('/src/hermes (+8 more)')
    const tr = (k, f) => ({ 'transfers.sourcesMore': '%1 (e mais %2)' })[k] || f
    expect(sourceSummary({ source: sources[0], sources, source_count: 9 }, '/home/me', 38, tr)).toBe('/src/hermes (e mais 8)')
  })
})

describe('describeOverall', () => {
  const base = { total_bytes: 50e9, overall_total_bytes: 100e9, overall_pct: 50 }
  it('shows an exact total plainly', () => {
    const o = describeOverall({ ...base, overall_total_kind: 'exact', overall_total_source: 'rsync_dry_run', overall_eta: '5m' })
    expect(o.show).toBe(true)
    expect(o.label).toBe('Overall progress')
    expect(o.line).toBe('50.0 GB / 100.0 GB (50%)')
    expect(o.line.includes('≈')).toBe(false)
    expect(o.etaText).toBe('ETA 5m')
    expect(o.fraction).toBeCloseTo(0.5)
    expect(o.dim).toBe(false)
  })
  it('marks estimates with ≈', () => {
    const o = describeOverall({ ...base, overall_total_kind: 'estimate', overall_total_source: 'du', overall_eta: '5m' })
    expect(o.label).toBe('Overall progress (estimate: size of the source)')
    expect(o.line).toBe('50.0 GB / ≈ 100.0 GB (≈ 50%)')
    expect(o.etaText).toBe('ETA ≈ 5m (estimate)')
    expect(describeOverall({ ...base, overall_total_kind: 'estimate', overall_total_source: 'device' }).label)
      .toBe("Overall progress (estimate: device's used space)")
    expect(describeOverall({ ...base, overall_total_kind: 'estimate', overall_total_source: 'batch' }).label)
      .toBe('Overall progress (estimate: what this batch copied)')
  })
  it('dims an upper bound and gives no ETA', () => {
    const o = describeOverall({ ...base, overall_total_kind: 'upper_bound', overall_total_source: 'du', overall_eta: '5m', overall_eta_reason: 'upper_bound' })
    expect(o.dim).toBe(true)
    expect(o.label).toBe('Overall progress (at most: size of the sources)')
    expect(o.line).toBe('50.0 GB / ≤ 100.0 GB (≥ 50%)')
    expect(o.etaText).toBe('no ETA: the total is only an upper bound')
  })
  it('never sticks at 100%: beyond the estimate', () => {
    const o = describeOverall({ total_bytes: 120e9, overall_total_bytes: 100e9, overall_pct: 99.9, overall_beyond: true,
      overall_total_kind: 'estimate', overall_total_source: 'du', overall_eta_reason: 'beyond' })
    expect(o.beyond).toBe(true)
    expect(o.fraction).toBe(1)
    expect(o.line).toBe('120.0 GB so far, more than the estimated ≈ 100.0 GB')
    expect(o.etaText).toBe('')
    // A legacy payload past its total is beyond too.
    expect(describeOverall({ total_bytes: 120e9, overall_total_bytes: 100e9, overall_pct: 100, overall_is_estimate: true }).beyond).toBe(true)
  })
  it('caps the % below 100 while running', () => {
    const o = describeOverall({ total_bytes: 100e9, overall_total_bytes: 100e9, overall_pct: 100, overall_total_kind: 'exact', overall_beyond: false })
    expect(o.line).toBe('100.0 GB / 100.0 GB (99.9%)')
  })
  it('explains a missing ETA', () => {
    const o = (reason) => describeOverall({ ...base, overall_total_kind: 'exact', overall_eta_reason: reason }).etaText
    expect(o('measuring')).toBe('measuring speed…')
    expect(o('paused')).toBe('paused, no ETA')
    expect(o('stalled')).toBe('stalled, no ETA')
    expect(o('too_long')).toBe('more than 3 days left')
    expect(o('beyond')).toBe('')
    expect(o('')).toBe('')
  })
  it('shows verification progress by list position', () => {
    const o = describeOverall({ kind: 'verification', total_bytes: 21e9, overall_total_bytes: 0, overall_pct: 42, progress_basis: 'list',
      // The collector's real shape: no byte total, so no total kind either.
      overall_total_kind: '', overall_total_source: '', overall_is_estimate: false, overall_estimate_kind: '',
      overall_eta: '4m', verify: { list_fraction: 0.42 } })
    expect(o.show).toBe(true)
    expect(o.label).toBe('Verification progress (position in the list)')
    expect(o.line).toBe('42% of the list checked')
    expect(o.fraction).toBeCloseTo(0.42)
    expect(o.etaText).toBe('ETA ≈ 4m (estimate)')
    // A pipe as the list: no fraction, nothing to show.
    expect(describeOverall({ kind: 'verification', progress_basis: 'list', overall_total_bytes: 0, verify: { list_fraction: -1 } }).show).toBe(false)
  })
  it('keeps the old labels for older collectors', () => {
    expect(describeOverall({ ...base, overall_is_estimate: true, overall_estimate_kind: 'device' }).label).toBe("Overall progress (estimate: device's used space)")
    expect(describeOverall({ ...base, overall_is_estimate: true, overall_estimate_kind: 'source' }).label).toBe('Overall progress (estimate: size of the source)')
    expect(describeOverall({ ...base, overall_is_estimate: false }).label).toBe('Overall progress')
  })
  it('shows nothing without a total, or for a dry run', () => {
    expect(describeOverall({ total_bytes: 5, overall_total_bytes: 0 }).show).toBe(false)
    expect(describeOverall({ ...base, kind: 'simulation', overall_total_kind: 'exact' }).show).toBe(false)
    expect(describeOverall(null).show).toBe(false)
  })
})

describe('describeScan and describePlan', () => {
  it('describes each scan state', () => {
    expect(describeScan({ scan: { state: 'queued' } })).toBe('Waiting to work out what is left to copy')
    expect(describeScan({ scan: { state: 'scanning', found_bytes: 5e9 } })).toBe('Working out what is left to copy… 5.00 GB found so far')
    expect(describeScan({ scan: { state: 'scanning', found_bytes: 0 } })).toBe('Working out the total size…')
    expect(describeScan({ scan: { state: 'failed', reason: 'timeout' } })).toBe('Could not work out the total size')
    expect(describeScan({ scan: { state: 'skipped', reason: 'remote' } })).toBe('Remote transfer: the total is not known')
    expect(describeScan({ scan: { state: 'done' } })).toBe('')
    expect(describeScan({ kind: 'simulation', scan: { state: 'queued' } })).toBe('')
  })
  it('falls back to overall_scanning', () => {
    expect(describeScan({ overall_scanning: true })).toBe('Working out the total size…')
    expect(describeScan({ overall_scanning: false })).toBe('')
  })
  it('shows what rsync will copy', () => {
    const plan = { selection_bytes: 113e9, to_copy_bytes: 68.7e9, already_there_bytes: 44.3e9 }
    expect(describePlan({ rsync_plan: plan })).toBe('To copy: 68.7 GB (113.0 GB selected, 44.3 GB already at the destination)')
    expect(describePlan({ rsync_plan: { selection_bytes: 10e9, to_copy_bytes: 4e9 } }))
      .toBe('To copy: 4.00 GB (10.0 GB selected, 6.00 GB already at the destination)')
    expect(describePlan({ rsync_plan: null })).toBe('')
    expect(describePlan({})).toBe('')
  })
})

describe('describeBatch', () => {
  const b = { name: 'copy.sh', state: 'running', phase: 'transfer', transfers: 2, elapsed_str: '23m', total_bytes: 79.6e9 }
  it('says what the batch is doing', () => {
    expect(describeBatch({ ...b, state: 'paused' }).status).toBe('paused')
    expect(describeBatch({ ...b, state: 'paused' }).paused).toBe(true)
    expect(describeBatch(b).status).toBe('2 running now')
    expect(describeBatch({ ...b, phase: 'simulation', transfers: 0 }).status).toBe('checking (dry run)')
    expect(describeBatch({ ...b, phase: 'verification', transfers: 0 }).status).toBe('verifying')
    expect(describeBatch({ ...b, phase: 'other', transfers: 0, current_commands: ['find', 'sort'] }).status).toBe('between steps: find, sort')
    expect(describeBatch({ ...b, phase: 'other', transfers: 0, current_commands: [] }).status).toBe('between steps')
    expect(describeBatch({ ...b, phase: 'other', transfers: 0 }).between).toBe(true)
  })
  it('adds up what its transfers copied', () => {
    expect(describeBatch(b).info).toBe('running for 23m · 79.6 GB copied by its transfers')
    expect(describeBatch(b).steps).toBe('')
    expect(describeBatch({ ...b, steps_done: 3, verified_bytes: 50e9 }).steps).toBe('Steps finished: 3 · 50.0 GB verified')
    expect(describeBatch({ ...b, steps_done: 1 }).steps).toBe('Steps finished: 1')
  })
  it('does not claim 0 B copied when it joined a script midway', () => {
    expect(describeBatch({ ...b, total_bytes: 0 }).info).toBe('running for 23m')
    expect(describeBatch({ ...b, total_bytes: 0, steps_done: 2 }).info).toBe('running for 23m · 0 B copied by its transfers')
  })
})

describe('notificationFor', () => {
  it('builds each notification', () => {
    expect(notificationFor({ type: 'transfer_finished', notify: true, tool: 'rsync', total_bytes: 47.4e9, elapsed_str: '27m' }))
      .toEqual({ title: 'Transfer finished', body: 'rsync: 47.4 GB in 27m' })
    expect(notificationFor({ type: 'transfer_stopped', notify: true, tool: 'rsync', total_bytes: 7.2e9, overall_total_bytes: 20e9, elapsed_str: '5m' }))
      .toEqual({ title: 'Transfer stopped early', body: 'rsync stopped at 7.20 GB of 20.0 GB' })
    expect(notificationFor({ type: 'transfer_stopped', notify: true, tool: 'cp', total_bytes: 7.2e9, overall_total_bytes: 0, elapsed_str: '5m' }).body)
      .toBe('cp (7.20 GB in 5m)')
    expect(notificationFor({ type: 'verification_finished', notify: true, tool: 'sha256sum', total_bytes: 1, verified_bytes: 113e9, elapsed_str: '1h 05m' }))
      .toEqual({ title: 'Verification finished', body: 'sha256sum: 113.0 GB read in 1h 05m' })
    expect(notificationFor({ type: 'batch_finished', notify: true, name: 'copy.sh', total_bytes: 160e9, elapsed_str: '2h 10m' }))
      .toEqual({ title: 'Batch finished', body: 'copy.sh: 160.0 GB copied in 2h 10m' })
  })
  it('stays quiet when it should', () => {
    expect(notificationFor({ type: 'transfer_finished', notify: false, tool: 'rsync' })).toBeNull()
    expect(notificationFor({ type: 'something_else', notify: true })).toBeNull()
    expect(notificationFor(null)).toBeNull()
  })
  it('translates and neutralizes markup', () => {
    const tr = (k, f) => ({ 'notify.batchFinished.title': 'Lote encerrado', 'notify.batchFinished.body': '%1: %2 copiados em %3' })[k] || f
    const n = notificationFor({ type: 'batch_finished', notify: true, name: '<b>x</b>&.sh', total_bytes: 1e9, elapsed_str: '1m' }, tr)
    expect(n.title).toBe('Lote encerrado')
    expect(n.body.includes('<')).toBe(false)
    expect(n.body.includes('&')).toBe(false)
    expect(n.body.endsWith('copiados em 1m')).toBe(true)
  })
})

describe('dev/mock/transfers.json', () => {
  const mock = JSON.parse(readFileSync(join(root, 'dev', 'mock', 'transfers.json'), 'utf8'))
  it('follows the v2 contract', () => {
    for (const t of mock.transfers) {
      expect('kind' in t, `kind in ${t.pid}`).toBe(true)
      expect('overall_total_kind' in t, `overall_total_kind in ${t.pid}`).toBe(true)
    }
    const kinds = mock.transfers.map((t) => t.kind)
    expect(kinds).toContain('simulation')
    expect(kinds).toContain('verification')
    expect(mock.count).toBe(mock.transfers.length)
    expect(mock.transfer_count + mock.check_count).toBe(mock.count)
    expect(Array.isArray(mock.events)).toBe(true)
  })
  it('has no total kind without a byte total, like the collector', () => {
    for (const t of mock.transfers) {
      if (!(t.overall_total_bytes > 0)) {
        expect(t.overall_total_kind, `overall_total_kind of ${t.pid}`).toBe('')
        expect(t.overall_total_source, `overall_total_source of ${t.pid}`).toBe('')
        expect(t.overall_is_estimate, `overall_is_estimate of ${t.pid}`).toBe(false)
        expect(t.overall_estimate_kind, `overall_estimate_kind of ${t.pid}`).toBe('')
      }
    }
  })
  it('keeps its aggregates consistent', () => {
    const copies = mock.transfers.filter((t) => t.kind === 'transfer')
    expect(mock.agg_total_bytes).toBe(copies.reduce((a, t) => a + t.total_bytes, 0))
    const b = mock.batches[0]
    const inBatch = copies.filter((t) => t.batch && t.batch.key === b.key)
    expect(b.total_bytes).toBe(b.done_bytes + inBatch.reduce((a, t) => a + t.total_bytes, 0))
  })
})

describe('i18n catalogs', () => {
  const catalog = (code) => JSON.parse(readFileSync(join(root, 'i18n', code + '.json'), 'utf8'))
  const en = catalog('en')
  const languages = readdirSync(join(root, 'i18n')).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5))
  it('every language has exactly the English keys', () => {
    for (const code of languages) {
      expect(Object.keys(catalog(code)).sort(), `keys of ${code}.json`).toEqual(Object.keys(en).sort())
    }
  })
  it('every key the QML and Model.js use exists in en.json', () => {
    const used = new Set()
    for (const file of readdirSync(root).filter((f) => f.endsWith('.qml') || f === 'Model.js')) {
      const text = readFileSync(join(root, file), 'utf8')
      for (const m of text.matchAll(/\b(?:tr\.t|monitor\.t|translate)\(\s*(?:tr,\s*)?"([a-zA-Z0-9_.]+)"/g)) used.add(m[1])
    }
    expect(used.size > 40, `found ${used.size} keys`).toBe(true)
    for (const key of used) expect(key in en, `missing key ${key}`).toBe(true)
  })
  it('placeholders match English in every language', () => {
    const marks = (s) => (s.match(/%\d/g) || []).sort().join(',')
    for (const code of languages) {
      const c = catalog(code)
      for (const key of Object.keys(en)) if (key in c) expect(marks(c[key]), `${code}: ${key}`).toBe(marks(en[key]))
    }
  })
})
