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
  toolIcon, toolDisplayTitle, formatRecentTransfer, diskOpenPath, shortenPath, parseJsonSafely
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
    expect(formatRecentTransfer({ tool: 'rsync', total_bytes: 1536, elapsed_str: '3m 05s' }, tr)).toBe('rsync (1.5 KB em 3m 05s)')
    expect(formatRecentTransfer({ tool: 'rsync', total_bytes: 1536, elapsed_str: '3m 05s' })).toBe('rsync (1.5 KB in 3m 05s)')
  })
  it('does not break when the size or the time is missing', () => {
    expect(formatRecentTransfer({ tool: 'cp', total_bytes: 0, elapsed_str: '20s' }, tr)).toBe('cp (20s)')
    expect(formatRecentTransfer({ tool: 'cp', total_bytes: '', elapsed_str: '' }, tr)).toBe('cp')
    expect(formatRecentTransfer(null, tr)).toBe('')
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
