# More Simple FTP

A small, no-fuss FTP client for Windows (and anywhere Python runs), with:

- **MoreSimpleFTP** – a modern two-pane window: your computer on the left, the server on the right.
- **ftpclient** – a command-line client with `ls`, `cd`, `pwd`, `lcd`, `get`, `put`, `mkdir`, `rm`, `rmdir [-r]`, and `rename`.

The repository also keeps `ftpgui.py`, an earlier classic-style window. It runs from source but isn't part of the installer.

## The app

- **FTP and FTPS** (explicit TLS). Certificates are checked; for a self-signed certificate you're asked before connecting anyway. Works with servers that require TLS session reuse (vsftpd, FileZilla Server).
- **Saved servers**: save connection details by name and switch between them from the dropdown (**Servers…** to add, edit or delete). Passwords can optionally be remembered in Windows Credential Manager. They are never written to the settings file.
- **Drag and drop** between panes. Drop onto a folder to transfer into it. Folders transfer with everything inside them.
- **Breadcrumb path bar**: click any part of the path to jump there, or click the empty space to type a path.
- **Transfer progress** with speed, queue count and Cancel.
- Light and dark themes that follow Windows (the ◐ button cycles System → Light → Dark), and a collapsible activity log.

| Key | Action |
| --- | --- |
| Enter / double-click | Open folder, or send a file to the other side |
| Backspace | Up one folder |
| F5 | Refresh |
| F2 | Rename |
| Delete | Delete (asks first) |
| Ctrl+Shift+N | New folder |
| Ctrl+A | Select all |

Settings and saved servers live in `%USERPROFILE%\.moresimpleftp.json`.

## Install (Windows)

Download `MoreSimpleFTP-<version>-setup.exe` from the [Releases](../../releases) page (or from the latest **Build** run under the Actions tab) and run it. No admin rights are needed.

The installer isn't code-signed yet, so Windows SmartScreen may say "Windows protected your PC". Click **More info → Run anyway**. It adds a Start menu shortcut, and `ftpclient.exe` sits next to the app for command-line use.

## Use the command line

```
ftpclient ftp.example.com -u alice                  # interactive shell, prompts for password
ftpclient ftp.example.com -u alice -p pw ls /pub    # one command, then exit
ftpclient ftp.example.com --tls get report.pdf      # FTPS
ftpclient ftp.example.com -- rmdir -r old_folder    # use -- before commands with flags
```

Inside the shell, type `help` for the command list.

## Run from source

Python 3.10+ with Tk (included with the python.org installer):

```
pip install -r requirements.txt   # optional: lets the app remember passwords
python more_simple_ftp.py
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
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest tests
```

The tests run a real FTP server locally (pyftpdlib) and exercise listing, transfers, rename and delete, plus the app's transfer worker, list parsing and saved-server settings.

To release a new version, bump `__version__` in `ftpclient.py` and `APP_VERSION` in `more_simple_ftp.py`, then push a tag such as `v0.2.0`.
