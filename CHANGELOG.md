# What's new in More Simple FTP

## 1.0.0 (2026-11-27)

### Saved connections
- Save the servers you use so you don't have to retype them. Fill in the boxes, press **Save** (or **Remember it** in the Crayon look), and give it a name.
- Pick a saved server from the list at the top to fill everything in. The last one you used is filled in when the app opens.
- You can choose to remember the password. It's kept in Windows Credential Manager, never in a plain file.
- **Forget** removes a saved server and its password.
- The command line uses the same list: `ftpclient work`, `--save`, `--saved` and `--forget`.

### Looks
- Pick a look under **Settings → Look**: Crayon "Simple" (the new default), Clean modern, Midnight (dark), Big friendly buttons, or Classic (the original plain window). Your choice is remembered.

### Other
- **Help → About** shows the version with the Hamilton Group Programming logo, and the app has its own crayon-style icon.
- **Help → What's new** shows this page.

## 0.1.0 (2026-11-27)

First version.
- Two-pane window: your computer on the left, the server on the right.
- Upload and download files and whole folders, with a progress bar.
- New folder, rename and delete, on either side.
- Optional FTP over TLS (FTPS).
- `ftpclient` command-line tool with `ls`, `cd`, `pwd`, `lcd`, `get`, `put`, `mkdir`, `rm`, `rmdir -r` and `rename`.
- Windows installer, no admin rights needed.
