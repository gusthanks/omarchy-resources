// Model.js - Helper utilities and data formatters for gusthx.transfers

function formatBytes(bytes) {
  if (bytes === undefined || bytes === null || isNaN(bytes) || bytes <= 0) return "0 B"
  var units = ["B", "KB", "MB", "GB", "TB", "PB"]
  var i = 0
  var val = Number(bytes)
  while (val >= 1024 && i < units.length - 1) {
    val /= 1024
    i++
  }
  return (i === 0 ? Math.round(val) : val.toFixed(1)) + " " + units[i]
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
  if (!tool) return "\uf021" // sync icon
  var t = String(tool).toLowerCase()
  if (t.indexOf("rsync") >= 0) return "\uf021" // sync arrows
  if (t.indexOf("rclone") >= 0) return "\uf0c2" // cloud
  if (t.indexOf("curl") >= 0 || t.indexOf("wget") >= 0 || t.indexOf("aria") >= 0 || t.indexOf("dlp") >= 0) return "\uf019" // download
  if (t.indexOf("git") >= 0) return "\uf126" // git branch
  if (t.indexOf("cp") >= 0 || t.indexOf("mv") >= 0) return "\uf0c5" // files / copy
  if (t.indexOf("dd") >= 0) return "\uf0a0" // hard disk
  if (t.indexOf("pacman") >= 0 || t.indexOf("yay") >= 0 || t.indexOf("paru") >= 0) return "\uf187" // package
  if (t.indexOf("tar") >= 0 || t.indexOf("zstd") >= 0) return "\uf1c6" // archive
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
