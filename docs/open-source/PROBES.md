# Experimental DJ adapters

These are this project's independent diagnostic and read-only adapter implementations. No proprietary application, raw memory capture, real diagnostic snapshot, licensed media, or modified third-party executable is distributed here.

- `serato_probe.py` reports local process / IPC conditions; `serato_reader.py` validates and reads supported live snapshots.
- `rekordbox_ax.py` uses the project's named macOS Accessibility helper for supported original-app title / time observations. It requires the user's own permission and manual song confirmation; it does not provide automatic original-app library control.
- The other rekordbox modules are version-specific experiments. They require the exact supported local test environment and do not automatically prepare or modify a third-party application.
- Parser unit tests use synthetic inputs. Passing them does not certify a new software version, controller, venue, or television.

Start with `README.md` and silent replay. Actual adapter use must be a deliberate user action against software and media that the user is authorized to access. Do not run real-app probes as an automatic CI check.
