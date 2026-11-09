#!/usr/bin/env bash
# Install the committed state of this repo into the live Omarchy plugin.
# Run this only when the work is finished and tested in the dev harness. It
# restarts the shell once at the end so the new version actually shows.
#
# If the live plugin folder is a plain copy (no .git), it is moved to
# ~/Work/backups/<plugin-id>.<timestamp> first and replaced by a git checkout
# of this repo, so later deploys are a fast-forward.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
id="$(jq -r .id "$repo/manifest.json")"
live="${OMARCHY_PLUGIN_DIR:-$HOME/.config/omarchy/plugins}/$id"
branch="$(git -C "$repo" branch --show-current)"

[[ -n $branch ]] || { echo "Check out a branch first (deploy installs that branch, HEAD is detached)." >&2; exit 1; }
[[ -z $(git -C "$repo" status --porcelain) ]] || { echo "Commit your changes first (deploy installs HEAD)." >&2; exit 1; }
# Validate exactly what gets installed (HEAD), not the working folder: a local
# node_modules/ (ignored by git) would make validate fail on its symlinks.
export_dir="$(mktemp -d)"
trap 'rm -rf "$export_dir"' EXIT
git -C "$repo" archive HEAD | tar -x -C "$export_dir"
omarchy plugin validate "$export_dir"
rm -rf "$export_dir"; trap - EXIT
"$repo/dev/run.sh" check >/dev/null || { "$repo/dev/run.sh" check; echo "QML does not compile; not deploying." >&2; exit 1; }

if [[ -d $live/.git ]]; then
  git -C "$live" pull --ff-only -q "$repo" "$branch"
else
  # Build the checkout outside the plugins folder first (the shell would load
  # a second copy there), so a failed clone leaves the live plugin untouched;
  # then swap it in.
  new="$HOME/Work/backups/.$id.new"
  rm -rf "$new"
  mkdir -p "$(dirname "$new")"
  git clone -q --branch "$branch" "$repo" "$new"
  # Keep `omarchy plugin update` pointing at GitHub when the repo has a remote.
  url="$(git -C "$repo" remote get-url origin 2>/dev/null || true)"
  [[ -n $url ]] && git -C "$new" remote set-url origin "$url"
  if [[ -d $live ]]; then
    backup="$HOME/Work/backups/$id.$(date +%Y%m%d-%H%M%S)"
    mkdir -p "$(dirname "$backup")"
    mv "$live" "$backup"
    # If the swap fails, put the plain copy back.
    trap '[[ -d $live ]] || mv "$backup" "$live"' EXIT
    echo "Backed up the plain copy at $live to $backup"
  fi
  mv "$new" "$live"
  trap - EXIT
fi
echo "Deployed $(git -C "$repo" log --oneline -1) to $live"

# The shell's hot reload is not reliable for plugins (the old version can stay
# on screen), so finish with one restart. Skip with NO_RESTART=1.
if [[ ${NO_RESTART:-0} != 1 ]]; then
  omarchy restart shell >/dev/null 2>&1 || true
  echo "Shell restarted: the new version is live."
fi
