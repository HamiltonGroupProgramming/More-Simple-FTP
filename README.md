# More Simple FTP

A small, no-fuss FTP client for Windows (and anywhere Python runs), with:

- **MoreSimpleFTP** – a classic two-pane window: your computer on the left, the server on the right. Upload, download (whole folders too), new folder, rename, delete, and optional FTP over TLS.
- **ftpclient** – a command-line client with `ls`, `cd`, `pwd`, `lcd`, `get`, `put`, `mkdir`, `rm`, `rmdir [-r]`, and `rename`.

## Change the look

Pick **Settings → Look** in the window to switch between five looks. Your choice is remembered next time.

- **Crayon "Simple"** (the default): hand-drawn boxes and crayon-coloured buttons.
- **Clean modern**: light and tidy, with the buttons in a toolbar on top.
- **Midnight (dark)**: dark mode with a monospace font. F5 uploads, F6 downloads.
- **Big friendly buttons**: large text and big "Put on server" / "Bring to my computer" buttons.
- **Classic (plain)**: the original plain window.

Settings are saved in `%APPDATA%\MoreSimpleFTP\settings.json` on Windows (`~/.config/MoreSimpleFTP/settings.json` elsewhere).

## Install (Windows)

Download `MoreSimpleFTP-<version>-setup.exe` from the [Releases](../../releases) page (or from the latest **Build** run under the Actions tab) and run it. No admin rights are needed. It adds a Start menu shortcut, and `ftpclient.exe` sits next to the app for command-line use.

## Use the command line

```
ftpclient ftp.example.com -u alice                  # interactive shell, prompts for password
ftpclient ftp.example.com -u alice -p pw ls /pub    # one command, then exit
ftpclient ftp.example.com --tls get report.pdf      # FTPS
ftpclient ftp.example.com -- rmdir -r old_folder    # use -- before commands with flags
```

Inside the shell, type `help` for the command list.

## Run from source

Python 3.9+ with Tk (included with the python.org installer):

```
python ftpgui.py
python ftpclient.py --help
```

## Build the installer yourself

On Windows with Python and [Inno Setup 6](https://jrsoftware.org/isinfo.php) installed:

```
powershell -ExecutionPolicy Bypass -File build.ps1
```

The installer lands in `dist\installer\`. Every push also builds it on GitHub Actions; pushing a `v*` tag publishes it as a GitHub release.

## Tests

```
pip install -r requirements-dev.txt
python -m pytest tests
```

The tests run a real FTP server locally (pyftpdlib) and exercise listing, transfers, rename and delete.
