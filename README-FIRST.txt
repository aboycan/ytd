ytd 1.2.1 - YouTube downloader (portable edition for Windows, macOS and Linux)
=============================================================================

No installation needed: extract this folder anywhere and run it. The first
run downloads what it needs INTO THIS FOLDER (runtime/): a portable Python if
you don't have one, yt-dlp, FFmpeg and Deno. Nothing is installed system-wide
and no administrator password is needed. Later runs start instantly.
Downloads are verified with SHA-256 checksums.

Supported: Windows 10/11, macOS 12+, Linux - on Intel/AMD (x64) and ARM64.
Internet is needed (for the first-run setup and, of course, for YouTube).


MACOS / LINUX  (Terminal)
-------------------------
  cd path/to/ytd-1.2.1
  ./ytd "https://www.youtube.com/watch?v=XXXXXXXXXXX"

  If you see "permission denied", run it once as:  sh ytd "<URL>"
  Optional - use "ytd" from any folder:          ./ytd --add-to-path


WINDOWS
-------
  Easiest: double-click "Start ytd.cmd", paste a YouTube link, choose
  video or audio. (The first time it sets itself up - a few minutes.)

  Or in PowerShell / Command Prompt:
  cd path\to\ytd-1.2.1
  .\ytd.cmd "https://www.youtube.com/watch?v=XXXXXXXXXXX"

  If the zip came from the internet: right-click the zip > Properties >
  tick "Unblock" > OK, BEFORE extracting it.
  Optional - use "ytd" from any folder:          .\ytd.cmd --add-to-path


EXAMPLES  (Windows: use .\ytd.cmd instead of ./ytd)
---------------------------------------------------
  ./ytd "<URL>"                                  video, 1080p (default)
  ./ytd "<URL>" -r 720p                          video, 720p
  ./ytd "<URL>" --type audio                     MP3, 320 kbps
  ./ytd "<URL with &list=...>"                   only that one video
  ./ytd "<URL with &list=...>" --list            the whole playlist
  ./ytd "<playlist URL>" --list --type audio     playlist as MP3s
  ./ytd "<playlist URL>" --list --limit 10       first 10 items
  ./ytd "<URL>" --info                           show details, no download
  ./ytd                                          interactive mode
  ./ytd --doctor                                 check everything is OK
  ./ytd --update                                 update yt-dlp (fixes most YouTube errors)
  ./ytd --setup                                  repair / redo the first-run setup
  ./ytd --help                                   all options

  ALWAYS put the URL in "double quotes".
  Files are saved in the "downloads" folder inside this folder
  (change with -o <folder>).


GOOD TO KNOW
------------
- Moving/copying this folder to another computer is fine; the setup runs
  again automatically for that computer's system (runtime/ keeps one setup
  per system, e.g. runtime/macos-arm64, runtime/windows-x64).
- To uninstall: delete this folder (and, if you used --add-to-path,
  ~/.local/bin/ytd on macOS/Linux or the PATH entry on Windows).
- Only download content you have the right to download.
- Something not working? See TROUBLESHOOTING.txt (and run ./ytd --doctor).
- Full documentation: README.md
