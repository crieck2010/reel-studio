@echo off
REM =====================================================================
REM  Update reel-studio - double-click, no command line needed.
REM
REM  1. git-pulls this repo (reel-studio itself)
REM  2. upgrades the twelve peer engines (survey-viz, survey-currents,
REM     survey-animate, survey-layout, survey-style, survey-schedule,
REM     survey-cache, survey-derive, survey-publish, survey-timescales,
REM     survey-aesthetics, survey-gazetteer):
REM       - if a peer lives as a git checkout next to this folder,
REM         it is git-pulled and reinstalled from that checkout
REM       - otherwise it is upgraded straight from GitHub
REM  3. prints the installed peer versions for confirmation
REM
REM  Run this any time a new reel-studio or survey-viz release is
REM  announced, then launch with the "Reel Studio" desktop icon.
REM
REM  NOTE: pulls run with `git -c gc.auto=0`. On Windows, git's
REM  automatic housekeeping (gc) can hang forever on an interactive
REM  "Deletion of directory .git\objects\... failed, try again? (y/n)"
REM  prompt when OneDrive or Defender holds a lock on .git. Disabling
REM  auto-gc for these pulls keeps the update non-interactive; run
REM  `git gc` by hand occasionally if a repo feels slow.
REM =====================================================================
setlocal
cd /d "%~dp0"

where git >nul 2>nul
if errorlevel 1 (
    echo ERROR: git was not found. Install it with:
    echo     winget install Git.Git
    echo then close and re-open this window.
    pause
    exit /b 1
)

echo === reel-studio ===
git -c gc.auto=0 pull --ff-only
if errorlevel 1 echo WARNING: git pull failed for reel-studio - continuing anyway.
echo.

for %%P in (survey-viz survey-currents survey-animate survey-layout survey-style survey-schedule survey-cache survey-derive survey-publish survey-timescales survey-aesthetics survey-gazetteer) do (
    if exist "..\%%P\.git" (
        echo === %%P  [local checkout] ===
        git -C "..\%%P" -c gc.auto=0 pull --ff-only
        if errorlevel 1 echo WARNING: git pull failed for %%P - reinstalling from the checkout anyway.
        python -m pip install --upgrade "..\%%P"
    ) else (
        echo === %%P  [from GitHub] ===
        python -m pip install --upgrade "git+https://github.com/crieck2010/%%P.git"
    )
    echo.
)

echo === Installed versions ===
python -c "import viz; print('survey-viz       ', viz.__version__)" 2>nul || echo survey-viz        not installed
python -c "import currents; print('survey-currents  ', currents.__version__)" 2>nul || echo survey-currents   not installed
python -c "import animate; print('survey-animate     ', animate.__version__)" 2>nul || echo survey-animate      not installed
python -c "import layout; print('survey-layout      ', layout.__version__)" 2>nul || echo survey-layout       not installed (platform layouts unavailable)
python -c "import style; print('survey-style       ', style.__version__)" 2>nul || echo survey-style        not installed (style presets unavailable)
python -c "import schedx; print('survey-schedule    ', schedx.__version__)" 2>nul || echo survey-schedule     not installed (scheduled generation unavailable)
python -c "import cachex; print('survey-cache       ', cachex.__version__)" 2>nul || echo survey-cache        not installed (render caching unavailable)
python -c "import derive; print('survey-derive      ', derive.__version__)" 2>nul || echo survey-derive       not installed (derived anomaly products unavailable)
python -c "import publish; print('survey-publish     ', publish.__version__)" 2>nul || echo survey-publish      not installed (social publishing unavailable)
python -c "import timescales; print('survey-timescales  ', timescales.__version__)" 2>nul || echo survey-timescales   not installed (suggested time windows unavailable)
python -c "import aesthetics; print('survey-aesthetics  ', aesthetics.__version__)" 2>nul || echo survey-aesthetics   not installed (mapped.earth presets unavailable)
python -c "import gazetteer; print('survey-gazetteer   ', gazetteer.__version__)" 2>nul || echo survey-gazetteer    not installed (place labels unavailable)

echo.
echo Done. Launch with the "Reel Studio" desktop icon.
pause
