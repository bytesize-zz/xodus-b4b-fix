# xodus-eac-fix

Fix for EasyAntiCheat online play on Xodus Nexus (Linux).

Games using EAC from the Microsoft Store fail to play online through Xodus Nexus because the Proton EAC Runtime does not recognize the GDK variant of the EAC bootstrapper. This tool automatically fixes it.

**Confirmed working:** Back 4 Blood (online co-op with EAC validated)

## The problem

MS Store games ship a **WinGDK** build of `EasyAntiCheat_EOS_Setup.exe`. The Proton EAC Runtime only recognizes the **Win64** (Steam) variant. Additionally, MS Store packages often lack `runtime.conf`, the signed file that authorizes EAC to run in Proton/Linux mode.

| Issue | Result |
|-------|--------|
| Missing `runtime.conf` | EAC never enters Proton mode → anti-cheat violation |
| GDK bootstrapper | "Unknown file version" → EAC validation fails → no online |

## How the fix works

1. **`runtime.conf` transplant** — Copies the EAC Proton authorization file from the Steam installation (both versions share the same EAC deployment IDs, so the file is interchangeable)

2. **Chain intercept** — Xodus validates file integrity *before* creating its ephemeral chain directory, but the game process reads files *after*. An `inotifywait` watcher detects when the chain copies `EasyAntiCheat_EOS_Setup.exe` and immediately replaces the GDK copy with the Steam Win64 variant. The original game files are never modified.

```
Xodus validates (GDK, 962560 bytes) ✓
    → creates chain → copies EAC exe
        → watcher swaps GDK → Win64 in chain
            → game reads Win64 → Proton EAC Runtime validates ✓
                → online play works ✓
```

## Requirements

- Back 4 Blood installed via Xodus Nexus
- `inotify-tools` package (installer handles this automatically)

## Install

```bash
git clone https://github.com/hortelaa/xodus-b4b-fix.git
cd xodus-b4b-fix
bash install.sh
```

The installer will:
- Detect your Back 4 Blood installation (Xodus Nexus + Steam)
- Verify that EAC deployment IDs match between both versions
- Copy `runtime.conf` to the Xodus installation
- Install the chain watcher script
- Create a desktop shortcut

## Usage

1. Run **"B4B EAC Fix"** from your app menu (or `b4b-eac-fix.sh` in the Xodus directory)
2. Launch Back 4 Blood through Xodus Nexus normally
3. The watcher intercepts and fixes the EAC bootstrapper automatically
4. Play online — `Ctrl+C` in the watcher terminal when done

## Technical details

Both the Steam and MS Store versions of Back 4 Blood share identical EAC deployment identifiers:

```
productid:    3bc138d96fb24d9597d4bbff23b9ecee
sandboxid:    57dfd95548214a138218e56cd9e5b9d8
deploymentid: e052f3c4fce741bab494ed0304eeaf62
```

The Proton EAC Runtime `.so` files in the Xodus Nexus runtime are byte-identical to Steam's `Proton EasyAntiCheat Runtime/v2/`. The only incompatibility is the bootstrapper binary format (GDK vs Win64).

### File comparison

| File | MS Store (GDK) | Steam (Win64) |
|------|---------------|---------------|
| `EasyAntiCheat_EOS_Setup.exe` | 962,560 bytes | 960,752 bytes |
| `runtime.conf` | Missing | Present (1,108 bytes) |
| `Settings.json` productid | `3bc138d9...` | `3bc138d9...` (same) |
| EAC `.so` runtime | Identical | Identical |

## Adapting for other games

This fix likely works for any EAC game where:
1. The Steam version works online with Proton
2. The MS Store version fails with "Unknown file version" or anti-cheat violation
3. Both versions share the same EAC deployment IDs (`productid`/`sandboxid`/`deploymentid` in `Settings.json`)

To adapt, edit `install.sh` and `b4b-eac-fix.sh` with the correct game paths.

## Potential Xodus Nexus integration

This fix could be integrated directly into the Nexus backend during chain assembly. See [PR_PLAN.md](PR_PLAN.md) for a detailed proposal with implementation phases, testing plan, and risk assessment.

## License

MIT
