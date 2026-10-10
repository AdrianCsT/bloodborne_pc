# Releasing

How the maintainer ships the Windows fork. Decided 2026-10-09, after the release list started to look like a testing playground. Read it once, then follow the checklist.

## Channels

- **Stable**: a normal GitHub release. Everyone gets it.
- **Beta**: a GitHub pre-release. Testers get it first.

A beta becomes stable after a day or two of play with no regression reported. Do not ship a stable build that never ran as a beta. The one exception so far is 1.6.17: 1.6.16 with only the launcher changed, which brought the "Beta versions" switch to stable players.

A published tag and zip never change. If a beta needs a fix, publish the next beta.

## Versions and tags

| Channel | Version | Tag |
|---|---|---|
| Stable | `MAJOR.MINOR.PATCH`, for example `1.7.0` | `windows-v1.7.0` |
| Beta | `MAJOR.MINOR.PATCH-beta.N`, for example `1.7.0-beta.1` | `windows-v1.7.0-beta.1` |

Order, lowest first: `1.6.17 < 1.7.0-beta.1 < 1.7.0-beta.2 < 1.7.0`.

The constant `VERSION` in `launcher/bbport_launcher_win.py` must match the tag without the `windows-v` prefix. Set it in the commit you tag.

The launcher compares versions, so its comparison must follow the order above, with a beta below its stable release. Test that before the first beta ships.

## Branches

- Work and betas live on `develop`.
- `master` fast-forwards to `develop` whenever a release ships, beta or stable (decided 2026-10-10; until then it moved only on stable releases).

## What the launcher offers

The update check offers stable releases to everyone. It offers pre-releases when the "Beta versions" switch is on, and not otherwise. That switch is on by default in a beta build. Stable builds have it from 1.6.17 on. Launchers older than 1.6.17 read only the latest stable release, so they never offer a beta.

## Release notes template

Release notes cover only what is new in that version. Features, requirements, install steps, long-standing known issues and credits live in `README.md`, and the notes link to it.

Copy this for every release. Keep the section order. Delete a section that has nothing to say, except the SHA-256 line, which is always there.

```markdown
<One or two sentences: what the program is, that the download contains no game files, and a link to the README for features, requirements and install steps. For a beta, add that it is a beta and ask testers to copy their `user` folder first.>

## New in <version>

<What players notice, then the cause or fix in a few words. A short paragraph or a short list. Written for players. Name the people whose work it is.>

## What to test

<Betas only: numbered things for testers to try and report, and which file to send (`user\last_run.log`).>

## Known issues

<Only problems this version brings or touches, with the card, driver or step that triggers them and what to send.>

## Updating

<How the launcher finds this version, and what stays (saves and settings). For a beta, how to turn on the "Beta versions" switch.>

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
   - The `licenses` folder is complete: Intel XeSS, NVIDIA DLSS, ReShade, PkgTool, AMD FidelityFX SDK and FireBurn FSR-Vulkan.
   - `Bloodborne.exe` and `BLauncher.exe` are there.
7. **Hash.** Run `sha256sum dist/bbport-windows.zip` and put the value in the notes.
8. **Tag.** Push `develop`, tag the commit from step 1 as `windows-v<version>`, and push the tag.
9. **Publish.** Create the release from the tag, with the notes from the template and the zip attached as `bbport-windows.zip`. Title: `Bloodborne for Windows <version>`.
   - Beta: tick **Set as a pre-release**, or pass `--prerelease` to `gh release create`.
   - Stable: leave it unticked.
   - Files the launcher downloads from a release go on the release its pin names, with their source. Today that is `tools/fetch_fsr4vk.py`: `fsr4vk-v0.4.3-amdfix.zip` and `fsr4vk-v0.4.3-amdfix-src.zip` on `windows-v1.7.0-beta.2`. After publishing, download the pinned URL once and compare its SHA-256 with the pin.
10. **Check the updater.** Open `BLauncher.exe` from the previous stable build and check for updates.
    - Stable release: it must offer the new version.
    - Beta: with the "Beta versions" switch off it must not offer the beta. With the switch on it must offer it.
11. **Move `master`.** Fast-forward `master` to the release commit and push it.

## Beta to stable

1. Testers play the beta for a day or two. If one reports a regression, fix it on `develop` and ship the next beta.
2. When a beta is clean, set `VERSION` to the stable number on `develop`. That edit (with the changelog entry) is the sole change since the last beta. Run the checklist again; the stable zip has its own hash.
3. Step 11 of the checklist moves `master` to the stable commit, as it does for every release.
