# deluge-piece-priority installer for Windows.
#
#   irm https://raw.githubusercontent.com/Jonny-GM/deluge-piece-priority/main/install.ps1 | iex
#
# While the repository is private the raw URL above 404s; fetch and run
# through the gh CLI (https://cli.github.com, after `gh auth login`)
# instead -- the script then also downloads the release itself through gh:
#
#   iex (@(gh api -H "Accept: application/vnd.github.raw" repos/Jonny-GM/deluge-piece-priority/contents/install.ps1) -join "`n")
#
# Or download it and run with options:
#   .\install.ps1 [-Version vX.Y.Z] [-PyVersion 3.12] [-Python <path>] [-ConfigDir <dir>] [-Uninstall]
#
# -Version defaults to the newest versioned release, falling back to the
# rolling "latest" build if none exists yet.
#
# The egg must match the Python that Deluge itself runs. On Windows that
# is almost always the interpreter *bundled inside the Deluge
# installation* (a python3xx.dll next to deluge.exe), not any separately
# installed Python -- so that is detected first; -PyVersion (e.g. 3.12)
# or -Python <interpreter> override the detection.
#
# Deluge's plugin loader never scans the general Python environment (see
# docs/spec/05-packaging-compat.md) -- it only looks in <config-dir>\
# plugins\, so this downloads the prebuilt .egg matching that Python
# version from the release, verifies its sha256 against the release's
# sha256sums.txt, and drops it there. Builds from source instead if no
# prebuilt egg matches (that path needs a real Python of the right
# version, plus setuptools). Does not install or configure Deluge itself,
# and does not enable the plugin -- see the printed next step for that.
param(
    [string]$Version = "",
    [string]$PyVersion = "",
    [string]$Python = "",
    [string]$ConfigDir = "",
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"

$Repo = "Jonny-GM/deluge-piece-priority"

# Release downloads go through the gh CLI whenever it's installed and
# authenticated: mandatory while the repository is private (unauthenticated
# requests to a private repo's releases return 404), a free rate-limit
# bump once it's public.
$UseGh = $false
if (Get-Command gh -ErrorAction SilentlyContinue) {
    gh auth status *> $null
    if ($LASTEXITCODE -eq 0) { $UseGh = $true }
}

function Get-ReleaseAsset([string]$Tag, [string]$Name, [string]$Dir) {
    if ($UseGh) {
        gh release download $Tag --repo $Repo --pattern $Name --dir $Dir --clobber
        if ($LASTEXITCODE -ne 0) { throw "gh release download failed for $Name (release '$Tag')" }
    } else {
        Invoke-WebRequest "https://github.com/$Repo/releases/download/$Tag/$Name" `
            -OutFile (Join-Path $Dir $Name) -UseBasicParsing
    }
}

# --- Deluge config directory ---------------------------------------------
# Matches deluge.common.get_default_config_dir()'s Windows branch: %APPDATA%\deluge.

if (-not $ConfigDir) {
    $ConfigDir = Join-Path $env:APPDATA "deluge"
}
$PluginsDir = Join-Path $ConfigDir "plugins"

# --- uninstall --------------------------------------------------------------

if ($Uninstall) {
    $eggs = Get-ChildItem $PluginsDir -Filter "PiecePriority-*.egg" -ErrorAction SilentlyContinue
    if (-not $eggs) {
        Write-Host ">> no PiecePriority egg found in $PluginsDir"
    } else {
        $eggs | Remove-Item -Force
        Write-Host ">> removed: $($eggs.FullName -join ', ')"
    }
    return
}

# --- pick the Python version to match the egg against -----------------------
# The egg must match the Python Deluge itself runs. On Windows that is
# almost always the interpreter bundled inside the Deluge installation
# (python3xx.dll next to deluge.exe), not any separately installed
# python.exe -- so the Deluge install directory is checked first. An
# explicit -PyVersion or -Python overrides.

function Get-InterpreterVersion([string]$Exe) {
    $v = & $Exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v -match '^\d+\.\d+$') { return $v }
    return $null
}

$PyVer = $null
if ($PyVersion) {
    $PyVer = $PyVersion
    Write-Host ">> targeting Python $PyVer (explicit -PyVersion)"
} elseif ($Python) {
    if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
        throw "python interpreter not found: $Python (see -Python)"
    }
    $PyVer = Get-InterpreterVersion $Python
    if (-not $PyVer) { throw "could not get a version from $Python" }
    Write-Host ">> targeting Python $PyVer ($Python)"
} else {
    # 1) Deluge's own bundled interpreter. General principle: find the
    #    real deluge executable — resolving launcher shims like scoop's,
    #    whose target path sits in a sibling .shim text file — and search
    #    around it for the python3xx.dll it ships with. Standard install
    #    roots are searched as a fallback for setups where nothing is on
    #    PATH.
    $roots = @()
    foreach ($name in 'deluged.exe', 'deluge.exe') {
        $c = Get-Command $name -ErrorAction SilentlyContinue
        if (-not $c) { continue }
        $roots += (Split-Path $c.Source)
        $shim = [System.IO.Path]::ChangeExtension($c.Source, '.shim')
        if (Test-Path $shim) {
            $target = (Select-String -Path $shim -Pattern '^\s*path\s*=\s*"?([^"]+)"?' |
                Select-Object -First 1).Matches.Groups[1].Value
            if ($target) { $roots += (Split-Path $target.Trim()) }
        }
    }
    $roots += (Join-Path $env:ProgramFiles 'Deluge')
    if (${env:ProgramFiles(x86)}) { $roots += (Join-Path ${env:ProgramFiles(x86)} 'Deluge') }
    $roots += (Join-Path $env:LOCALAPPDATA 'Programs\Deluge')
    foreach ($d in ($roots | Where-Object { $_ } | Select-Object -Unique)) {
        $dll = Get-ChildItem $d -Recurse -Depth 3 -Filter 'python3*.dll' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^python3(\d+)\.dll$' } |
            Select-Object -First 1
        if ($dll) {
            $minor = [regex]::Match($dll.Name, '^python3(\d+)\.dll$').Groups[1].Value
            $PyVer = "3.$minor"
            Write-Host ">> targeting Python $PyVer (Deluge's bundled interpreter: $($dll.FullName))"
            break
        }
    }
    # 2) A real interpreter on PATH. The WindowsApps 'python.exe' is a
    #    Microsoft Store stub that prints an install prompt and exits
    #    nonzero, so it is skipped rather than trusted.
    if (-not $PyVer) {
        foreach ($name in 'py', 'python3', 'python') {
            $c = Get-Command $name -ErrorAction SilentlyContinue
            if (-not $c -or $c.Source -like '*\WindowsApps\*') { continue }
            $v = Get-InterpreterVersion $c.Source
            if ($v) {
                $Python = $c.Source
                $PyVer = $v
                Write-Host ">> targeting Python $PyVer ($Python)"
                break
            }
        }
    }
}
if (-not $PyVer -or $PyVer -notmatch '^\d+\.\d+$') {
    throw "could not determine the Python version Deluge runs under. Pass -PyVersion (e.g. -PyVersion 3.12 -- check for a python3xx.dll in your Deluge install folder) or -Python <interpreter>."
}

# --- resolve release tag -----------------------------------------------------
# The GitHub API's "latest" endpoint only ever returns the newest
# non-prerelease release, so it correctly skips the rolling "latest"
# prerelease that release.yml republishes on every push to main -- that
# one is only used as a fallback below, same as install.sh.

if (-not $Version) {
    try {
        if ($UseGh) {
            $Version = gh api "repos/$Repo/releases/latest" --jq .tag_name 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $Version) { throw "no versioned release" }
        } else {
            $Version = (Invoke-RestMethod "https://api.github.com/repos/$Repo/releases/latest").tag_name
        }
    } catch {
        Write-Host ">> no versioned release found; installing the rolling latest build"
        $Version = "latest"
    }
}

$tmp = Join-Path $env:TEMP "deluge-piece-priority-install"
if (Test-Path $tmp) { Remove-Item -Recurse -Force $tmp }
New-Item -ItemType Directory -Force $tmp | Out-Null

# --- try a prebuilt egg first -------------------------------------------------

Write-Host ">> checking release '$Version' for a Python $PyVer build"
$egg = $null
try {
    Get-ReleaseAsset $Version "sha256sums.txt" $tmp
    $sums = Get-Content (Join-Path $tmp "sha256sums.txt") -Raw
    $line = $sums -split "`r?`n" | Where-Object { $_ -match "-py$([regex]::Escape($PyVer))\." } | Select-Object -First 1
} catch {
    $line = $null
}

if ($line) {
    $parts = $line.Trim() -split "\s+", 2
    $expected, $artifact = $parts[0], $parts[1].Trim()
    $eggPath = Join-Path $tmp $artifact

    Write-Host ">> downloading $artifact"
    Get-ReleaseAsset $Version $artifact $tmp

    $actual = (Get-FileHash -Algorithm SHA256 $eggPath).Hash.ToLower()
    if ($actual -ne $expected.ToLower()) {
        throw "checksum mismatch for ${artifact}: expected $expected, got $actual"
    }
    Write-Host ">> checksum verified"
    $egg = $eggPath
} else {
    Write-Host ">> no prebuilt egg for Python $PyVer in release '$Version'; building from source"
    if (-not $Python) {
        throw "building from source needs a real Python $PyVer interpreter, but only a version was detected (Deluge's bundled Python can't run builds). Install Python $PyVer and pass -Python <path>, or pick a release that ships a py$PyVer egg."
    }
    $builderVer = Get-InterpreterVersion $Python
    if ($builderVer -ne $PyVer) {
        throw "-Python is $builderVer but the egg must be py$PyVer (eggs are tied to Deluge's own Python minor version)"
    }
    & $Python -c "import setuptools" 2>$null
    if ($LASTEXITCODE -ne 0) { throw "setuptools is required to build from source ($Python -m pip install setuptools)" }
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "git is required to build from source" }

    $src = Join-Path $tmp "src"
    if ($UseGh) {
        gh repo clone $Repo $src -- --quiet --depth 1 --branch $Version
    } else {
        git clone --quiet --depth 1 --branch $Version "https://github.com/$Repo.git" $src
    }
    if ($LASTEXITCODE -ne 0) { throw "could not clone $Repo @ $Version" }

    Push-Location $src
    & $Python -c "from setuptools import setup; setup()" bdist_egg | Out-Null
    Pop-Location

    $egg = Get-ChildItem (Join-Path $src "dist") -Filter "*.egg" | Select-Object -First 1 -ExpandProperty FullName
    if (-not $egg) { throw "build did not produce an .egg" }
    Write-Host ">> built $(Split-Path $egg -Leaf)"
}

# --- install ------------------------------------------------------------------

New-Item -ItemType Directory -Force $PluginsDir | Out-Null
Get-ChildItem $PluginsDir -Filter "PiecePriority-*.egg" -ErrorAction SilentlyContinue | Remove-Item -Force
Copy-Item $egg $PluginsDir -Force
Write-Host ">> installed $(Split-Path $egg -Leaf) -> $PluginsDir\"

Remove-Item -Recurse -Force $tmp

Write-Host ">> restart deluged (and deluge-web, if you use it), then enable 'PiecePriority' from Preferences > Plugins"
