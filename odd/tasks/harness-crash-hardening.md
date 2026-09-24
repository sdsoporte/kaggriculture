# Feature: harness crash hardening (Pi TUI heap abort + tmpfs pressure)

Goal: prevent a repeat of the 2026-09-23 SIGABRT of the Pi TUI (`Reached heap
limit`, RSS 4.69 GB, terminal left in raw mode) and reduce memory/disk pressure
on the workstation, without changing the user's TUI mode (fullscreen stays).

Evidence: `/var/crash/_usr_bin_node.1002.crash` (297 MB, `Signal 6`,
`VmHWM 4690372 kB`), Node v24.19.0 default `heap_size_limit` = 4288 MB.
Transcript bloat came from multi-hundred-KB bash outputs (hard-coded
`BASH_OUTPUT_LIMIT = 100 * 1024` in Pi, not configurable). The corpus download
and the gzip decompression were exonerated as causes.

Decisions taken by the user before implementation:
1. Delete all `/var/crash` dumps owned by us (306 MB). — DONE
2. Keep `tuiMode: fullscreen` (mitigate via heap ceiling + output discipline + fresh sessions).
3. Clean `/tmp` now and automate the hygiene. — DONE
4. Disable apport (requires sudo; user runs the handed-off command). — PENDING USER

## Tasks

- [x] T1 Harden the Pi heap: `NODE_OPTIONS=--max-old-space-size=8192` in zsh/bash/fish rc, plus a `pi-safe` wrapper with a disk/RAM/swap preflight
- [x] T2 Add a global heavy-output discipline policy to `~/.pi/agent/APPEND_SYSTEM.md`
- [x] T3 `/tmp` hygiene: clean now, then a systemd user timer for stale/oversized `pi-bash-*.log`
- [x] T4 Remove our `/var/crash` dumps (306 MB) and hand off the exact sudo command to disable apport
- [x] T5 Runbook: document incident, symptoms and hardening in `docs/OPERATIONS.md` and commit the work unit
- [x] T6 Final verification: Node honors `NODE_OPTIONS`, timer scheduled, resources re-measured, new file set valid

## Constraints / non-goals

- No change to `tuiMode` (user decision).
- No killing of other live Pi sessions (arc-paper-track and revelo sessions are active).
- Harness files live outside the repo (`~/.pi`, `~/.local/bin`, shell rc, `~/.config/systemd/user`); only `docs/OPERATIONS.md` and this task file are committed.
- The corpus download constraint is RELEASED: it finished (608 files, 20 GB, `scratch/corpus_full/`).

## Evidence log

- 2026-09-23 22:49:40 — SIGABRT, PID 803984, cwd this repo, tty /dev/pts/13.
- 2026-09-23 23:0x — recon: 370/608 corpus files (12.95 GB), 40 GB free disk, swap 7.0/8.0 GB used, `/tmp` 1.1 GB (tmpfs).
- 2026-09-23 23:14 — T1: `NODE_OPTIONS` block added to `~/.zshrc:139`, `~/.bashrc:138`, `~/.config/fish/config.fish:130`; `~/.local/bin/pi-safe` (2341 bytes, mode 755).
- 2026-09-24 02:2x — T2: `heavy-output-discipline` section appended to `~/.pi/agent/APPEND_SYSTEM.md` (the `herdr-subagent-routing` block is untouched).
- 2026-09-24 02:24 — T3: `~/.local/bin/pi-tmp-hygiene` created and syntax-checked (`bash -n` OK); first real run removed 5 logs, freed 33.7 MiB (`/tmp` still 1.2 GB, dominated by other projects' payloads: `arc3-replays` 771 MB, `bun-linux-x64` 76 MB, `wheels` 66 MB). `pi-tmp-hygiene.timer` + `.service` enabled in the user manager; proof run `Result=success`, `ExecMainStatus=0`; next elapse 2026-09-24 03:02:22 -03; `Persistent=yes`.
- 2026-09-24 02:2x — T4: deleted `/var/crash/_usr_bin_node.1002.crash` (297 MB), `_usr_bin_zsh.1002.crash` (2.4 MB) and `_usr_libexec_localsearch-3.1002.crash` (7.0 MB); total 306 MB. `/var/crash` now 8.0 KB holding only root-owned `kexec_cmd`. Apport disable command handed to the user.
- 2026-09-24 02:2x — T5: section `5.F. Host Harness Hardening (Pi TUI heap abort)` added to `docs/OPERATIONS.md`.
- 2026-09-24 02:2x — T6: see verification block below.

## Verification (T6)

- **Heap ceiling raised and honoured**: `node -p "require('v8').getHeapStatistics().heap_size_limit/1048576"` inside an interactive zsh returns **8384 MB**, i.e. the requested 8192 MB old space + 192 MB V8 overhead, against the **4288 MB** default that aborted on 2026-09-23 (+4.1 GB headroom).
- **All three shells export the flag**: `zsh -ic`, `bash -ic` and `fish -c` each report `NODE_OPTIONS=--max-old-space-size=8192`, and the rc guard prevents duplicate flags.
- **`pi-safe` preflight works**: with forced thresholds it printed the disk and swap warnings and then exec'd `pi --version` successfully (`0.87.1`).
- **Syntax**: `bash -n` clean on `~/.local/bin/pi-tmp-hygiene` and `~/.local/bin/pi-safe`; both mode 755.
- **Timer scheduled**: `systemctl --user list-timers --all` shows `pi-tmp-hygiene.timer` active, next elapse 2026-09-24 03:02:22 -03, `Persistent=yes`; a manual `start` returned `Result=success` / `ExecMainStatus=0`.
- **Crash dumps**: only the root-owned `kexec_cmd` remains in `/var/crash` (8.0 KB, was 306 MB).
- **Resources re-measured**: RAM 31.3 GB total / 17.1 GB available; `/` 93% used (33 GB free, driven by the 20 GB corpus); `/tmp` (tmpfs, 16 GB) 1.2 GB used; largest live Node process 349 MB RSS.
- **Open risk, not fixed by this feature**: swap is at **93% (7688/8191 MB)**. Killing other live sessions was an explicit non-goal, so this is reported for the user to act on (it is the most likely trigger of the next OOM/thrash).
