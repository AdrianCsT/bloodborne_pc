# Releasing

How the maintainer ships the Windows fork. Decided 2026-10-09, after the release list started to look like a testing playground. Read it once, then follow the checklist.

## Channels

- **Stable**: a normal GitHub release. Everyone gets it.
- **Beta**: a GitHub pre-release. Testers get it first.

A beta becomes stable after a day or two of play with no regression reported. Do not ship a stable build that never ran as a beta.

A published tag and zip never change. If a beta needs a fix, publish the next beta.

## Versions and tags

| Channel | Version | Tag |
|---|---|---|
| Stable | `MAJOR.MINOR.PATCH`, for example `1.7.0` | `windows-v1.7.0` |
| Beta | `MAJOR.MINOR.PATCH-beta.N`, for example `1.7.0-beta.1` | `windows-v1.7.0-beta.1` |

Order, lowest first: `1.6.16 < 1.7.0-beta.1 < 1.7.0-beta.2 < 1.7.0`.

The constant `VERSION` in `launcher/bbport_launcher_win.py` must match the tag without the `windows-v` prefix. Set it in the commit you tag.

The launcher compares versions, so its comparison must follow the order above, with a beta below its stable release. Test that before the first beta ships.

## Branches

- Work and betas live on `develop`.
- `master` stays where it is until a stable release ships, then fast-forwards to the stable commit.

## What the launcher offers

The update check offers stable releases to everyone. It offers pre-releases when the "Beta versions" switch is on, and not otherwise. That switch is on by default in a beta build. (The switch is being built now; this is the intended behavior.)

## Release notes template

Copy this for every release. Keep the section order. Delete a section that has nothing to say, except the SHA-256 line, which is always there.

```markdown
<One or two sentences: what the program is, and that the download contains no game files. For a beta, add a line saying it is a beta and what testers should try.>

## New in <version>

<What players notice, then the cause or fix in a few words. A short paragraph or a short list. Written for players.>

## What this build includes

<Standing features of the build, one line each. Change it when a feature is added or removed.>

## Fixes included

<Fixes carried over from earlier versions that players still ask about, one line each.>

## Known issues

<Problems you know about and have not fixed, with the card, driver or step that triggers them and what to send if a player sees them.>

## Requirements

<Windows version, graphics card and driver, memory, and the game version the build expects.>

## Install

<Numbered steps from unzip to PLAY.>

## Updating

<How the launcher finds the update, and what stays (saves and settings). For a beta, say how to turn on the "Beta versions" switch.>

## Reporting a problem

<Where to open an issue and which file to attach (`user\last_run.log`).>

## Credits

<Upstream projects and people whose work is in this build, and where the bundled licenses are (`licenses` folder in the zip).>

SHA-256 of `bbport-windows.zip`: `<hash>`
```

## Checklist

Run these in order. Stop at the first failure and fix it before you go on.

1. **Set the version.** Set `VERSION` in `launcher/bbport_launcher_win.py` and add the entry to `CHANGELOG.md`. Commit on `develop`.
2. **Tests.** Run `python -m pytest -q tests`. Required: 0 failed.
3. **Validation layer.** Run the game with the Vulkan validation layer on. Compare the VUIDs with the known baseline: `RuntimeSpirv-OpEntryPoint-08743`, `VkImageViewCreateInfo-usage-02275`, `vkCmdDraw-None-09600`. Any other VUID blocks the release.
4. **Play run.** Play for 5 minutes. Required: no device loss.
5. **Package.** Run `packaging/windows/package.sh`. It writes `dist/bbport-windows.zip`.
6. **Check the zip.**
   - It holds no game files (no `eboot.bin`, no `.pkg`, no dump).
   - The `licenses` folder is complete: Intel XeSS, NVIDIA DLSS, ReShade, PkgTool, AMD FidelityFX SDK and FireBurn FSR-Vulkan. (The last two are added to `package.sh` for 1.7.0-beta.1.)
   - `Bloodborne.exe` and `BLauncher.exe` are there.
7. **Hash.** Run `sha256sum dist/bbport-windows.zip` and put the value in the notes.
8. **Tag.** Push `develop`, tag the commit from step 1 as `windows-v<version>`, and push the tag.
9. **Publish.** Create the release from the tag, with the notes from the template and the zip attached as `bbport-windows.zip`. Title: `Bloodborne for Windows <version>`.
   - Beta: tick **Set as a pre-release**, or pass `--prerelease` to `gh release create`.
   - Stable: leave it unticked.
10. **Check the updater.** Open `BLauncher.exe` from the previous stable build and check for updates.
    - Stable release: it must offer the new version.
    - Beta: with the "Beta versions" switch off it must not offer the beta. With the switch on it must offer it.

## Beta to stable

1. Testers play the beta for a day or two. If one reports a regression, fix it on `develop` and ship the next beta.
2. When a beta is clean, set `VERSION` to the stable number on `develop`. That edit (with the changelog entry) is the sole change since the last beta. Run the checklist again; the stable zip has its own hash.
3. After step 10 passes, fast-forward `master` to the stable commit and push it.
