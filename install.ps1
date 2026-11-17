# Clipper installer for Windows (PowerShell 5.1 or 7).
#   irm https://raw.githubusercontent.com/guleriaakshit/clipper/main/install.ps1 | iex
# or, inside a clone:  powershell -ExecutionPolicy Bypass -File install.ps1 [-Yes]
# -Yes (or $env:CLIPPER_YES = "1") takes every default, no questions.
# Env: CLIPPER_DIR (install folder), CLIPPER_REPO (git URL), CLIPPER_STT_MODEL (existing model folder),
#      CLIPPER_SKIP (comma list of: stt,stt-hq,renderer,command,skill,open) for unattended installs.
# To update later: clipper update

$script:Yes = ($args -contains '-Yes') -or ($args -contains '--yes') -or ($env:CLIPPER_YES -eq '1')
$script:Repo = if ($env:CLIPPER_REPO) { $env:CLIPPER_REPO } else { 'https://github.com/guleriaakshit/clipper.git' }
$script:Skip = ",$($env:CLIPPER_SKIP),"
$script:SelfDir = $PSScriptRoot

function Say($m) { Write-Host $m }
function Step($m) { Write-Host ''; Write-Host $m -ForegroundColor Cyan }
function Ok($m) { Write-Host "  OK $m" -ForegroundColor Green }
function Warn($m) { Write-Host "  !  $m" -ForegroundColor Yellow }
function Die($m) { Write-Host ''; Write-Host "Clipper install stopped: $m" -ForegroundColor Red; throw 'clipper-stop' }
function Skipped($what) { $script:Skip -like "*,$what,*" }
function Have($c) { [bool](Get-Command $c -ErrorAction SilentlyContinue) }

function Ask($q, $def) {
  if ($script:Yes) { return ($def -eq 'y') }
  $hint = '[y/N]'; if ($def -eq 'y') { $hint = '[Y/n]' }
  $a = Read-Host "  $q $hint"
  if (-not $a) { $a = $def }
  return ($a -match '^[Yy]')
}

function AskVal($q, $def) {
  if ($script:Yes) { return $def }
  $shown = ''; if ($def) { $shown = " [$def]" }
  $a = Read-Host "  $q$shown"
  if (-not $a) { return $def }
  return $a
}

function Update-SessionPath {
  $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

# Need <command> <what it's for> <winget id>: $true when the command is there (installing it with winget if allowed)
function Need($cmd, $why, $wingetId) {
  if (Have $cmd) { Ok "$cmd found"; return $true }
  Warn "$cmd is missing ($why)."
  if ((Have winget) -and (Ask "Install it with winget now?" 'y')) {
    winget install --id $wingetId -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
    Update-SessionPath
    if (Have $cmd) { Ok "$cmd installed"; return $true }
    Warn "$cmd was installed, but this window can't see it yet. Open a new PowerShell window and run the installer again."
  }
  return $false
}

# A Python 3.11+ that isn't the Microsoft Store placeholder; prints its full path.
function Find-Python {
  if (Have py) {
    foreach ($v in '3.13', '3.12', '3.11', '3') {
      $exe = & py "-$v" -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null
      if ($LASTEXITCODE -eq 0 -and $exe) { return "$exe".Trim() }
    }
  }
  foreach ($c in 'python', 'python3') {
    $cmd = Get-Command $c -ErrorAction SilentlyContinue
    if (-not $cmd -or $cmd.Source -like '*WindowsApps*') { continue }
    $exe = & $c -c "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')" 2>$null
    if ($LASTEXITCODE -eq 0 -and $exe) { return "$exe".Trim() }
  }
  return $null
}

function Install-Clipper {
  Say 'Clipper finds where chat spiked in a stream (past or live) and cuts those moments into vertical Shorts.'

  # ------------------------------------------------------------ tools
  Step '1. Tools'
  if (-not (Need git 'to download and update Clipper' 'Git.Git')) { Die 'git is needed.' }
  if (-not (Need ffmpeg 'to cut and read video' 'Gyan.FFmpeg')) { Die 'ffmpeg is needed.' }
  $py = Find-Python
  if (-not $py) {
    Warn 'Python 3.11 or newer is missing.'
    if ((Have winget) -and (Ask 'Install Python 3.12 with winget now?' 'y')) {
      winget install --id Python.Python.3.12 -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
      Update-SessionPath
      $py = Find-Python
    }
    if (-not $py) { Die 'Install Python 3.11+ from python.org (tick "Add to PATH"), then run this again.' }
  }
  Ok "Python: $py"

  # ------------------------------------------------------------ folder
  Step '2. Folder'
  if ($script:SelfDir -and (Test-Path (Join-Path $script:SelfDir 'clipper\cli.py'))) {
    $dir = $script:SelfDir
    Ok "Installing in this copy: $dir"
  } else {
    $def = if ($env:CLIPPER_DIR) { $env:CLIPPER_DIR } else { Join-Path $HOME 'Clipper' }
    $dir = AskVal 'Install folder:' $def
    if (Test-Path (Join-Path $dir '.git')) {
      git -C $dir pull --ff-only -q
      if ($LASTEXITCODE -eq 0) { Ok "Updated $dir" } else { Warn "Couldn't update $dir; using it as it is." }
    } elseif (Test-Path (Join-Path $dir 'clipper\cli.py')) {
      Ok "Using the Clipper already in $dir"
    } elseif ((Test-Path $dir) -and (Get-ChildItem -Force $dir | Select-Object -First 1)) {
      Die "$dir exists and isn't Clipper. Pick an empty or new folder."
    } else {
      git clone -q --depth 1 $script:Repo $dir
      if ($LASTEXITCODE -ne 0) { Die "Couldn't download Clipper from $($script:Repo)." }
      Ok "Downloaded Clipper to $dir"
    }
  }
  $dir = (Resolve-Path $dir).Path
  Set-Location $dir
  foreach ($d in 'jobs', 'tmp', 'models') { New-Item -ItemType Directory -Force (Join-Path $dir $d) | Out-Null }
  $env:npm_config_cache = Join-Path $dir 'tmp\npm-cache'
  $env:PYTHONUTF8 = '1'
  $vpy = Join-Path $dir '.venv\Scripts\python.exe'

  # ------------------------------------------------------------ python env
  Step '3. Python packages'
  if (-not (Test-Path $vpy)) {
    & $py -m venv (Join-Path $dir '.venv')
    if ($LASTEXITCODE -ne 0) { Die "Couldn't make the Python environment in $dir\.venv." }
  }
  & $vpy -m pip install -q --upgrade pip
  & $vpy -m pip install -q -e $dir
  if ($LASTEXITCODE -ne 0) { Die "Couldn't install Clipper's Python packages (see the messages above)." }
  Ok 'Dashboard, chat readers, yt-dlp and face finder installed'

  # ------------------------------------------------------------ choices
  $confStt = ''
  Step '4. Speech-to-text (word-by-word subtitles)'
  Say '  Clipper uses a stream''s own subtitles when it has them (YouTube). When it doesn''t (Twitch, Kick, live), it'
  Say '  transcribes on this computer, offline and free: NVIDIA Parakeet TDT 0.6B v2 (~480 MB download).'
  Say '  Without it, Twitch and Kick clips get no subtitles; everything else works.'
  if (-not (Skipped 'stt') -and (Ask 'Set up speech-to-text?' 'y')) {
    & $vpy -m pip install -q 'sherpa-onnx>=1.12'
    if ($LASTEXITCODE -eq 0) { Ok 'sherpa-onnx installed' } else { Warn "Couldn't install sherpa-onnx." }
    $existing = $env:CLIPPER_STT_MODEL
    if (-not $existing) { $existing = AskVal 'Already have a Parakeet TDT 0.6B (v2 or v3) int8 model folder? Path, or Enter to download:' '' }
    if ($existing -and (Test-Path (Join-Path $existing 'encoder.int8.onnx')) -and (Test-Path (Join-Path $existing 'tokens.txt'))) {
      $confStt = $existing; Ok "Using the model in $existing"
    } else {
      if ($existing) { Warn "No model files in $existing, downloading instead." }
      & $vpy -m clipper stt-download
      if ($LASTEXITCODE -eq 0) { Ok 'Model ready' } else { Warn 'The model download failed. Try later with: clipper stt-download' }
    }
  } else {
    Say '  Skipped. Add it later with: clipper stt-download (after: .venv\Scripts\pip install sherpa-onnx)'
  }

  Step '4b. Better speech-to-text for past streams (NVIDIA GPU)'
  $gpu = $null
  if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) { $gpu = (& nvidia-smi --query-gpu=name --format=csv,noheader | Select-Object -First 1) }
  if (-not $gpu) {
    Say '  No NVIDIA GPU found: past streams use Parakeet too.'
  } else {
    Say "  Found $gpu. Past streams don't need real-time speed, so Clipper can use Qwen3-ASR 1.7B on the GPU instead:"
    Say '  more accurate, with punctuation. It needs its own Python environment with PyTorch (~9 GB in all, 6+ GB of GPU memory).'
    if (-not (Skipped 'stt-hq') -and (Ask 'Set up the GPU model?' 'y')) {
      & $vpy -m clipper stt-hq
      if ($LASTEXITCODE -eq 0) { Ok 'GPU speech-to-text ready' } else { Warn 'GPU setup failed; past streams use Parakeet. Try later with: clipper stt-hq' }
    } else {
      Say '  Skipped. Add it later with: clipper stt-hq'
    }
  }

  Step '5. Short renderer (Remotion)'
  Say '  Renders the 1080x1920 Short (face cam, gameplay, subtitles, effects, memes). Needs Node.js 18+ and ~400 MB.'
  if (-not (Skipped 'renderer') -and (Ask 'Set up the renderer?' 'y')) {
    $nodeOk = $false
    if (Have node) { node -e "process.exit(+process.versions.node.split('.')[0] >= 18 ? 0 : 1)"; $nodeOk = ($LASTEXITCODE -eq 0) }
    if (-not $nodeOk) { $nodeOk = Need node 'for the renderer' 'OpenJS.NodeJS.LTS' }
    if ($nodeOk) {
      Push-Location (Join-Path $dir 'remotion')
      npm.cmd ci --no-audit --no-fund --loglevel=error
      if ($LASTEXITCODE -eq 0) { Ok 'Remotion installed' } else { Warn "Couldn't install Remotion's packages." }
      npx.cmd --yes remotion browser ensure | Out-Null
      if ($LASTEXITCODE -eq 0) { Ok 'Headless Chrome for rendering ready' } else { Warn "Couldn't fetch Remotion's headless Chrome now; it downloads on the first render." }
      Pop-Location
    } else {
      Warn 'No Node.js 18+: skipping the renderer. Install Node.js and run the installer again.'
    }
  } else {
    Say '  Skipped. You can still find and preview clips; rendering needs this step (run the installer again).'
  }

  Step '6. Terminal command'
  if (-not (Skipped 'command') -and (Ask "Add a 'clipper' command to your terminal?" 'y')) {
    $bin = Join-Path $HOME '.local\bin'
    New-Item -ItemType Directory -Force $bin | Out-Null
    Set-Content -Path (Join-Path $bin 'clipper.cmd') -Value "@call `"$dir\clipper.cmd`" %*" -Encoding ASCII
    Ok "clipper -> $dir\clipper.cmd"
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    if (-not (($userPath -split ';') -contains $bin)) {
      if (Ask "$bin isn't on your PATH. Add it?" 'y') {
        $newPath = if ($userPath) { "$userPath;$bin" } else { $bin }
        [Environment]::SetEnvironmentVariable('Path', $newPath, 'User')
        Update-SessionPath
        Ok "Added. New terminal windows know 'clipper'."
      }
    }
  }

  $claudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
  if ((Test-Path $claudeDir) -and -not (Skipped 'skill')) {
    Step '7. Claude Code'
    Say '  /clip runs Clipper and writes what happens in each clip; /short edits a clip into a Short (the dashboard''s Send to Claude button uses it).'
    if (Ask 'Install the /clip and /short skills for Claude Code?' 'y') {
      foreach ($name in 'clip', 'short') {
        $src = Join-Path $dir "skills\$name\SKILL.md"
        $dst = Join-Path $claudeDir "skills\$name\SKILL.md"
        New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
        $same = (Test-Path $dst) -and ((Get-FileHash $src).Hash -eq (Get-FileHash $dst).Hash)
        if ($same) { Ok "/$name is up to date"; continue }
        if ((Test-Path $dst) -and -not (Ask "You already have a /$name skill. Replace it?" 'n')) { continue }
        Copy-Item $src $dst -Force
        Ok "Installed /$name"
      }
    }
  }

  # ------------------------------------------------------------ config
  & $vpy -c "import json, sys, time; from pathlib import Path; p = Path('config.json'); c = json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}; c.update({'stt_model': sys.argv[1]} if len(sys.argv) > 1 and sys.argv[1] else {}); c['installed'] = time.strftime('%Y-%m-%d'); p.write_text(json.dumps(c, indent=1), encoding='utf-8')" "$confStt"

  Step 'Done'
  Say "  Start the dashboard:  clipper serve   (or $dir\clipper.cmd serve)  then open http://localhost:8765"
  Say '  Past stream:          clipper analyze <Twitch VOD / YouTube / Kick VOD link>'
  Say '  Live stream:          clipper live <twitch.tv/channel, kick.com/channel or live YouTube link>'
  Say '  Get the latest:       clipper update'
  if (-not (Skipped 'open') -and (Ask 'Open the dashboard now?' 'n')) { & (Join-Path $dir 'clipper.cmd') serve }
}

try {
  Install-Clipper
} catch {
  if ("$_" -ne 'clipper-stop') { Write-Host "Clipper install stopped: $_" -ForegroundColor Red }
}
