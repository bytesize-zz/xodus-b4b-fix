# PR Plan: EAC GDK-to-Win64 compatibility for online play

## Summary

Games using EasyAntiCheat on MS Store (GDK builds) fail to play online through
Xodus Nexus because the Proton EAC Runtime does not recognize the GDK variant
of `EasyAntiCheat_EOS_Setup.exe`. This PR adds automatic detection and
remediation so that affected games can play online without manual workarounds.

**Confirmed working:** Back 4 Blood (online co-op with EAC validated)  
**Likely affected:** Hell Let Loose, Hell Let Loose: Vietnam, and any other
EAC game that works on Steam+Proton but fails through Xodus Nexus.

---

## Problem

### Root cause

The Proton EAC Runtime ships `.so` stubs (`easyanticheat_x64.so`, etc.) that
intercept EAC's Windows DLL calls and handle validation natively on Linux.
These stubs expect the **Win64** variant of `EasyAntiCheat_EOS_Setup.exe`.

MS Store games ship a **WinGDK** variant of the same file. While both contain
the same EOS product/sandbox/deployment IDs, the binary format differs:

| Variant | Size (B4B) | Recognized by Proton EAC Runtime |
|---------|-----------|----------------------------------|
| Win64 (Steam) | 960,752 bytes | Yes |
| WinGDK (MS Store) | 962,560 bytes | No → "Unknown file version" |

Additionally, MS Store packages often omit `runtime.conf` from the
`EasyAntiCheat/Certificates/` directory. This signed file authorizes EAC to
operate in Proton/Linux mode. Without it, EAC defaults to Windows-only
validation, detects Wine, and rejects the session.

### Error chain

1. `runtime.conf` missing → EAC never enters Proton mode → anti-cheat violation
2. `runtime.conf` present but GDK bootstrapper → "Unknown file version
   (EasyAntiCheat/EasyAntiCheat_EOS_Setup.exe)" → EAC validation fails →
   anti-cheat violation for online play

### Current workaround (manual)

1. Copy `runtime.conf` from Steam version's `EasyAntiCheat/Certificates/`
2. Run an `inotifywait` watcher that monitors `.xodus-chain-*` creation
3. When the chain copies `EasyAntiCheat_EOS_Setup.exe`, immediately replace it
   with the Steam Win64 variant
4. The game process reads the Win64 version → Proton EAC Runtime validates → online works

This works because Xodus validates file integrity BEFORE creating the chain,
but the game process reads files AFTER the chain is assembled.

---

## Proposed solution

### Approach: post-chain EAC fixup in the backend

The Nexus backend already creates the chain directory and assembles symlinks
and file copies. The fix adds a post-assembly step that detects EAC games
and applies compatibility fixups before handing off to the game process.

### Implementation phases

#### Phase 1: runtime.conf transplant (low risk, high impact)

**Where:** Nexus backend, launch preparation (after chain assembly)

**Logic:**
```
if game has EasyAntiCheat/Certificates/ directory:
    if runtime.conf is missing:
        check if Steam has the same game (match by EAC productid/sandboxid/deploymentid)
        if found:
            copy runtime.conf from Steam's EAC Certificates
            log: "EAC runtime.conf transplanted from Steam installation"
        else:
            check if Proton EAC Runtime v2 ships a generic runtime.conf
            (future: fetch from a known-good source)
```

**Validation:** Compare `productid`, `sandboxid`, `deploymentid` in both
`Settings.json` files. Only transplant if all three match.

#### Phase 2: EAC bootstrapper swap in chain (medium risk, high impact)

**Where:** Nexus backend, post-chain assembly (before game process launch)

**Logic:**
```
if game has EasyAntiCheat_EOS_Setup.exe in chain:
    read PE version info or file hash
    if file matches known GDK variant:
        locate Win64 variant (Steam installation with matching deployment IDs)
        if found:
            replace the COPY in the chain directory (not the original)
            log: "EAC bootstrapper swapped: GDK → Win64 for Proton compatibility"
```

**Key constraint:** The original file in the game directory is NEVER modified.
The swap only happens in the ephemeral chain copy. This preserves Xodus's
MSIXVC integrity validation.

#### Phase 3: EAC compatibility database (optional, future)

Maintain a JSON/TOML mapping of known EAC deployment IDs and their
compatibility status:

```json
{
  "3bc138d96fb24d9597d4bbff23b9ecee": {
    "game": "Back 4 Blood",
    "gdk_bootstrapper_sha256": "2cf5f353a0bf669e...",
    "win64_bootstrapper_sha256": "94567bf0dfcf48bf...",
    "runtime_conf_required": true,
    "online_verified": true
  }
}
```

This allows the fix to work even without a local Steam installation, by
bundling or downloading known-good Win64 bootstrappers.

---

## Files to modify

### Nexus backend (Rust)

| File/module | Change |
|-------------|--------|
| Launch preparation (chain assembly) | Add post-assembly EAC fixup hook |
| EAC detection | New module: scan chain for `EasyAntiCheat/` directory |
| runtime.conf transplant | New function: locate Steam EAC, validate IDs, copy |
| Bootstrapper swap | New function: replace GDK exe in chain with Win64 |
| Config/logging | Log all EAC fixup actions for debugging |

### Xodus Nexus (TypeScript/Electron)

| File | Change |
|------|--------|
| Wiki/Compatibility | Add Back 4 Blood as "Yes - Playable online" |
| Settings (optional) | Toggle for EAC compatibility fixup (default: on) |

### Documentation

| File | Change |
|------|--------|
| wiki/Compatibility.md | Add B4B entry with status and notes |
| wiki/EAC-Compatibility.md (new) | Document the GDK vs Win64 issue and fix |
| CHANGELOG.md | Entry for the new feature |

---

## Testing plan

### Manual test matrix

| Game | Store | EAC | Test |
|------|-------|-----|------|
| Back 4 Blood | MS Store / Game Pass | EOS | Launch → Fort Hope → online co-op match |
| Fallout 76 | MS Store / Game Pass | EOS | Launch → online (already works, regression test) |
| Hell Let Loose | MS Store / Game Pass | EOS | Launch → check if EAC crash is resolved |

### Automated checks

1. Unit test: EAC detection correctly identifies `EasyAntiCheat/` in chain
2. Unit test: `Settings.json` parser extracts productid/sandboxid/deploymentid
3. Unit test: ID comparison between Steam and MS Store variants
4. Integration test: chain assembly with EAC fixup produces correct files

---

## Issue template (to open first)

**Title:** `EAC games fail online: GDK bootstrapper incompatible with Proton EAC Runtime`

**Labels:** `bug`, `anti-cheat`, `compatibility`

**Body:**

```markdown
## Description

Games using EasyAntiCheat from the Microsoft Store cannot play online through
Xodus Nexus. The Proton EAC Runtime does not recognize the GDK (WinGDK) variant
of `EasyAntiCheat_EOS_Setup.exe`, producing "Unknown file version" and blocking
the EAC handshake needed for online play.

## Affected games

- Back 4 Blood (confirmed)
- Hell Let Loose (likely — crashes after EAC init)
- Hell Let Loose: Vietnam (likely — same crash)
- Any EAC game that works on Steam+Proton but fails on Xodus

## Steps to reproduce

1. Install Back 4 Blood via Xodus Nexus
2. Launch the game
3. Try to join an online match
4. Observe: "Online activity is disabled due to a game anti-cheat violation"

## Root cause

Two issues:
1. MS Store EAC packages lack `runtime.conf` in `Certificates/`
   (authorizes Proton mode)
2. The GDK `EasyAntiCheat_EOS_Setup.exe` binary format is not recognized
   by the Proton EAC Runtime `.so` stubs

## Verified fix

1. Copy `runtime.conf` from Steam's B4B installation (same EAC deployment IDs)
2. After Xodus creates the chain directory, replace the GDK bootstrapper
   copy with the Steam Win64 variant
3. Result: EAC validates in Proton mode → online play works

## Environment

- Xodus Nexus 0.3.9
- Nexus runtime 11.0-experimental
- CachyOS, kernel 7.3.1-1-cachyos
- Back 4 Blood (MS Store / Game Pass)

## Evidence

Both store versions share identical EAC deployment:
- productid: 3bc138d96fb24d9597d4bbff23b9ecee
- sandboxid: 57dfd95548214a138218e56cd9e5b9d8
- deploymentid: e052f3c4fce741bab494ed0304eeaf62

The Proton EAC Runtime `.so` files in the Nexus runtime are identical to
Steam's `Proton EasyAntiCheat Runtime/v2/` (same SHA256).
```

---

## PR checklist

- [ ] Open issue first with reproduction steps
- [ ] Branch from `develop` (per CONTRIBUTING.md)
- [ ] Implement Phase 1 (runtime.conf transplant)
- [ ] Implement Phase 2 (chain bootstrapper swap)
- [ ] Add logging for all EAC fixup actions
- [ ] Test B4B online co-op end-to-end
- [ ] Regression test Fallout 76 (EAC game that already works)
- [ ] Update wiki Compatibility page
- [ ] Create wiki page documenting EAC GDK compatibility
- [ ] PR targets `develop`, follows semantic versioning
- [ ] No credentials, raw diagnostics or private data in commits

---

## Risk assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Breaks games that currently work | Low | Only activates when EAC dir detected + GDK bootstrapper mismatch |
| Steam installation not available | Medium | Phase 3 adds bundled/downloadable known-good files |
| Future EAC updates change format | Low | SHA256 matching + version detection; logs warn on unknown variants |
| Maintainer prefers different approach | Medium | Open issue first to discuss before coding |

---

## Recommended workflow

1. **Open the issue** — get maintainer feedback on approach
2. **Start with Phase 1** (runtime.conf) — smallest change, biggest impact
3. **Phase 2** in same PR or follow-up — depends on maintainer preference
4. **Phase 3** as separate future PR — needs design discussion
