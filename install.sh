#!/usr/bin/env bash
# deluge-piece-priority installer for Linux and macOS.
#
#   curl -fsSL https://raw.githubusercontent.com/Jonny-GM/deluge-piece-priority/main/install.sh | bash
#
# While the repository is private the raw URL above 404s; fetch and run
# through the gh CLI (https://cli.github.com, after `gh auth login`)
# instead -- the script then also downloads the release itself through gh:
#
#   gh api -H "Accept: application/vnd.github.raw" repos/Jonny-GM/deluge-piece-priority/contents/install.sh | bash
#
# Options:
#   --version vX.Y.Z   install a specific release (default: newest versioned
#                       release, falling back to the rolling "latest" build
#                       if none exists yet)
#   --python PATH      python interpreter to match the egg against
#                       (default: whatever `deluged` on PATH actually runs
#                       under, falling back to `python3`)
#   --config-dir DIR   Deluge config directory (default: autodetected,
#                       same rule Deluge itself uses)
#   --uninstall        remove the installed egg
#
# Deluge's plugin loader never scans the general Python environment (see
# docs/spec/05-packaging-compat.md) -- it only looks in <config-dir>/
# plugins/, so this downloads the prebuilt .egg matching your Python
# version from the release, verifies its sha256 against the release's
# sha256sums.txt, and drops it there. Builds from source instead if no
# prebuilt egg matches your Python version. Does not install or configure
# Deluge itself, and does not enable the plugin -- see the printed next
# step for that.
set -euo pipefail

REPO="Jonny-GM/deluge-piece-priority"
API="https://api.github.com/repos/$REPO"
DL="https://github.com/$REPO/releases/download"

log() { printf '>> %s\n' "$*"; }
die() { printf 'install.sh: error: %s\n' "$*" >&2; exit 1; }

VERSION="" PYTHON="" CONFIG_DIR="" UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version) VERSION="${2:?--version needs an argument}"; shift 2 ;;
    --python) PYTHON="${2:?--python needs an argument}"; shift 2 ;;
    --config-dir) CONFIG_DIR="${2:?--config-dir needs an argument}"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) die "unknown option $1 (see --help)" ;;
  esac
done

command -v curl >/dev/null || die "curl is required"

# Release downloads go through the gh CLI whenever it's installed and
# authenticated: mandatory while the repository is private (unauthenticated
# requests to a private repo's releases return 404), a free rate-limit
# bump once it's public.
GH=0
if command -v gh >/dev/null && gh auth status >/dev/null 2>&1; then
  GH=1
fi

# fetch_asset TAG NAME DIR: download one release asset into DIR.
fetch_asset() {
  if [[ "$GH" == 1 ]]; then
    gh release download "$1" --repo "$REPO" --pattern "$2" --dir "$3" --clobber 2>/dev/null
  else
    curl -fsSL --retry 3 "$DL/$1/$2" -o "$3/$2" 2>/dev/null
  fi
}

# --- Deluge config directory --------------------------------------------
# Matches deluge.common.get_default_config_dir(): Deluge has no macOS
# special case, so this is the same XDG path on Linux and macOS.

if [[ -z "$CONFIG_DIR" ]]; then
  CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/deluge"
fi
plugins_dir="$CONFIG_DIR/plugins"

# --- uninstall -------------------------------------------------------------

if [[ "$UNINSTALL" == 1 ]]; then
  shopt -s nullglob
  eggs=("$plugins_dir"/PiecePriority-*.egg)
  shopt -u nullglob
  if [[ ${#eggs[@]} -eq 0 ]]; then
    log "no PiecePriority egg found in $plugins_dir"
  else
    rm -f "${eggs[@]}"
    log "removed: ${eggs[*]}"
  fi
  exit 0
fi

# --- pick a python interpreter to match the egg against --------------------

if [[ -z "$PYTHON" ]] && command -v deluged >/dev/null; then
  # Resolve deluged's own shebang, so the egg matches the interpreter
  # Deluge actually runs under rather than just whatever `python3` happens
  # to be on PATH (they can differ, e.g. Deluge installed into a venv).
  shebang="$(head -1 "$(command -v deluged)")"
  shebang="${shebang#\#!}"
  case "$shebang" in
    *env\ *) shebang="${shebang#*env }" ;;
  esac
  resolved="$(command -v "$shebang" 2>/dev/null || true)"
  [[ -n "$resolved" ]] && PYTHON="$resolved"
fi
[[ -n "$PYTHON" ]] || PYTHON="python3"
command -v "$PYTHON" >/dev/null || die "python interpreter not found: $PYTHON (see --python)"

pyver="$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
log "targeting Python $pyver ($PYTHON)"

# --- resolve release tag ----------------------------------------------------
# The GitHub API's "latest" endpoint only ever returns the newest
# non-prerelease release, so it correctly skips the rolling "latest"
# prerelease that release.yml republishes on every push to main -- that
# one is only used as a fallback below, same as install.sh in the sibling
# TorrentSeek repo.

if [[ -z "$VERSION" ]]; then
  if [[ "$GH" == 1 ]]; then
    VERSION="$(gh api "repos/$REPO/releases/latest" --jq .tag_name 2>/dev/null)" || true
  else
    VERSION="$(curl -fsSL "$API/releases/latest" 2>/dev/null \
      | grep -o '"tag_name" *: *"[^"]*"' | head -1 | sed 's/.*"\([^"]*\)".*/\1/')" || true
  fi
  if [[ -z "${VERSION:-}" ]]; then
    log "no versioned release found; installing the rolling latest build"
    VERSION=latest
  fi
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# --- try a prebuilt egg first ------------------------------------------------

log "checking release '$VERSION' for a Python $pyver build"
artifact=""
if fetch_asset "$VERSION" sha256sums.txt "$tmp"; then
  artifact="$(awk '{print $2}' "$tmp/sha256sums.txt" | grep -F -- "-py${pyver}." | head -1 || true)"
fi

if [[ -n "$artifact" ]]; then
  log "downloading $artifact"
  fetch_asset "$VERSION" "$artifact" "$tmp" || die "download failed for $artifact"
  grep -F "  $artifact" "$tmp/sha256sums.txt" > "$tmp/one.sum"
  if command -v sha256sum >/dev/null; then
    (cd "$tmp" && sha256sum -c one.sum >/dev/null) || die "checksum mismatch for $artifact"
  else
    (cd "$tmp" && shasum -a 256 -c one.sum >/dev/null) || die "checksum mismatch for $artifact"
  fi
  log "checksum verified"
  egg="$tmp/$artifact"
else
  # No prebuilt egg for this Python version in the release -- fall back to
  # building one locally from that release's source tag.
  log "no prebuilt egg for Python $pyver in release '$VERSION'; building from source"
  "$PYTHON" -c "import setuptools" 2>/dev/null \
    || die "setuptools is required to build from source ($PYTHON -m pip install setuptools)"
  command -v git >/dev/null || die "git is required to build from source"
  if [[ "$GH" == 1 ]]; then
    gh repo clone "$REPO" "$tmp/src" -- --quiet --depth 1 --branch "$VERSION" \
      || die "could not clone $REPO @ $VERSION"
  else
    git clone --quiet --depth 1 --branch "$VERSION" "https://github.com/$REPO.git" "$tmp/src" \
      || die "could not clone $REPO @ $VERSION"
  fi
  ( cd "$tmp/src" && "$PYTHON" -c "from setuptools import setup; setup()" bdist_egg >/dev/null )
  egg="$(find "$tmp/src/dist" -name '*.egg' | head -1)"
  [[ -n "$egg" ]] || die "build did not produce an .egg"
  log "built $(basename "$egg")"
fi

# --- install -----------------------------------------------------------------

mkdir -p "$plugins_dir"
rm -f "$plugins_dir"/PiecePriority-*.egg
install -m 0644 "$egg" "$plugins_dir/"
log "installed $(basename "$egg") -> $plugins_dir/"

log "restart deluged (and deluge-web, if you use it), then enable 'PiecePriority' from Preferences > Plugins"
