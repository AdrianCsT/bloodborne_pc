# Changelog

All notable changes to the Windows fork of the Bloodborne PS4 port. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Release tags are named `windows-v<version>`.

The release pages for 1.6 to 1.6.15 were deleted on 2026-10-09. The git tags remain, and this file replaces their notes. Version 1.6.9 is folded into 1.6.10 (see there).

## [Unreleased]

### Changed

- The frame generation switch is called "Frame generation" in the launcher and the in-game menu. Its old name, "Frame generation (FSR 3.1)", made it look like it switched the upscaler to FSR 3.1; it works on top of every upscaler, FSR 4 and 4.1.1 included. The log names it AMD frame interpolation.

## [1.7.0] - 2026-10-10

The stable release of the 1.7.0 betas below, plus these changes from their testing.

### Added

- Button icons that match how you play. The game draws PlayStation buttons; the launcher now redraws them at start as Xbox buttons (A, B, X, Y, LB, RB, LT, RT, LS, RS, Menu, View, d-pad arrows) or as the keys and mouse buttons you bound (keycaps with the first key of each input, WASD for the left stick, a mouse with the bound button lit). Controls > Button icons picks Automatic (from the controller connected at start: Xbox, PlayStation, or keyboard and mouse when none), PlayStation, Xbox or Keyboard and mouse. The icons are made from your own copy of the game, cached in `out\icons` and applied as a mod layer, so the game folder is untouched; a mod with its own `menu/common.tpf.dcx` wins and the log says the icons are off for that run. The set is chosen at start and stays until the next one.

### Changed

- The default keyboard keys follow Dark Souls III better: Esc opens the menu (equipment, items; it was Tab), Tab and G both open the gestures (Tab is new), and Space alone is Circle (dodge, hold to sprint; Esc is no longer Circle). Saved bindings in `bbport.ini` are not touched, so a key you saved for another input and that is now a default (Esc for Options, Tab for the left touchpad) presses both inputs; rebind one of them in the Controls tab.
- Keyboard diagonals (W with A or D) now push the left stick as far as a real stick's full push, along the circle, instead of into the square's corner, which is about 1.4 times as far. Letting go of one of the two keys no longer drops the push in one step. This fixes sprinting that ended when you sprinted straight, added A or D, and let it go: in a replay in the game, the stamina bar refilled right after the release with the old corner value (the sprint stopped) and kept draining with the new one. A sprint that starts on the diagonal was not affected either way.

### Fixed

- The in-game menu (Insert) could not save its settings to `bbport.ini` on Windows: the save renamed a temporary file over the existing one, which Windows refuses, and the log said `Settings: cannot write`. Found by building the settings tests on Windows; not yet seen in the game.
- The launcher found its updates through the GitHub account name, which changed from AdrianCsT to 0xCydral. GitHub forwards the old name only until someone else takes it and creates a repository of the same name, and from then on that person's releases would have been offered as updates. The launcher now looks releases up by the repository's id, which no rename changes, and downloads FSR 4.1.1 from the new address.
- A release can hold more than one zip (beta.2 also carries the fsr4vk build and its source). The launcher took the first zip GitHub listed; it now takes `bbport-windows.zip` by name, and for a release without it, Update opens the release page.
- Updating FSR 4.1.1 while the game was running could not replace the DLL the game had open. The launcher now asks you to close the game first, and a failed replace leaves no temporary file behind.
- The mouse camera hook kept its code page writable and executable at once. The page is now executable only, and the camera turn goes through a separate data page.

## [1.7.0-beta.2] - 2026-10-10

### Added

- A Controls tab in the Windows launcher (Advanced view), which the README already described ([#5](https://github.com/0xCydral/bloodborne_pc/issues/5)). It picks the controller and gives each input up to four keys and a gamepad button, saved as the `key.<input>=` and `pad.<input>=` lines the game already read from the Linux launcher. Keys are captured by their position, so AZERTY and Cyrillic layouts bind the key the game sees.
- Mouse and keyboard play ([#5](https://github.com/0xCydral/bloodborne_pc/issues/5)). While the game window has focus the mouse turns the camera; Insert or Alt+Tab lets go of it. On game 1.09 a hook in the game's camera code turns it directly, so the camera follows the mouse with no stick lag; elsewhere the mouse acts as the right stick. Mouse buttons and wheel steps can be bound, alone or with Shift, Ctrl or Alt, and the Dark Souls III layout is the default keyboard and mouse binding (a "Dark Souls III layout" button puts it back). The Controls tab also has the mouse sensitivity, inverted vertical look and an option to stop the camera turning by itself while walking (off by default). The camera hook is ported from [Ryansousa10's mouse and keyboard fork](https://github.com/Ryansousa10/bloodborne_windows_mouse_and_keyboard).
- A `Perf:` line in `user\last_run.log` every 30 seconds: frames per second, the slowest 1% of frames, the worst frame and the shader compiles in that window. A tester's log now says how the game ran.

### Changed

- Object motion vectors on AMD cards have their own switch under Object motion vectors, off by default, which needs Experimental features on. Turning on Experimental features for FSR 4.1.1 no longer turns them on.

### Fixed

- FSR 4.1.1 works on AMD cards. On AMD's Windows driver, fsr4vk v0.4.3 stopped at "descriptor binding exceeds queried layout size" and the game fell back to FSR 3.1. The launcher now downloads fsr4vk v0.4.3 with a fix for that driver, built by this port and hosted on its release next to its source, and offers it as "Update FSR 4.1.1" to anyone who has the original. Checked on a Radeon RX 6600 (driver 26.8.1) and a GeForce RTX 4070; reported upstream as [dvj5411/fsr4vk#1](https://github.com/dvj5411/fsr4vk/issues/1).
- The frame rate fell low and unsteady while another window had the focus. The game now asks Windows not to throttle it in the background (its clocks and its 1 ms timer). Not yet confirmed on the PC that showed it.
- Numpad 0 with Num Lock off opens the in-game menu, like Insert. Keyboards without a separate Insert key could only open it with L3+R3.
- In a window, an output above 1920x1080 was rendered at full size and then shrunk into a 1920x1080 window, and frame generation ran at 1920x1080. The window now opens at the output size, or maximized when that size does not fit the screen.
- The FSR 4.1.1 entry could stay gray after you turned on Experimental features or downloaded the files, because the launcher kept a GPU check made while they were off. It now ignores that result and checks again when the switch goes on.
- With object motion vectors on, some still objects got wrong vectors: in a test, bushes behind the Hunter's Dream workshop moved 100 to 650 pixels a frame, and FSR and frame generation smeared them. Where an object's vector differs from the camera's by more than 5% of the frame width (85 pixels at 1706x960, at least 32), the camera's vector is used. At the same moment of the test route, the worst frames went from 2.8% of the picture with such vectors to none in 24 frames.

## [1.7.0-beta.1] - 2026-10-09

### Added

- FSR 4.1.1 on Windows through [fsr4vk](https://github.com/dvj5411/fsr4vk) (experimental). No Windows driver offers the Vulkan extension the built-in FSR 4.1.1 needs, so the game can load fsr4vk's DLL instead. It is opt-in: turn on Experimental features (Advanced), then use "Download FSR 4.1.1" in Graphics (about 20 MB, SHA-256 checked, into `fsr4vk\` in the install, kept by updates), then pick FSR 4.1.1. Nothing from fsr4vk (GPLv3) is in the zip. On an RTX 4070 at 1080p from 720p it costs about 2.4 ms per frame, against 2.0 ms for FSR 4 and 0.55 ms for FSR 3.1. A saved FSR 4.1.1 falls back to DLSS or FSR 3.1 when the switch is off or the files are gone. `BB_FSR4VK_DIR` and `BB_FSR4VK=0` control it from the command line.
- An "Experimental features" switch (Advanced, Performance), off by default. It makes options marked experimental usable. Today that is FSR 4.1.1 and object motion vectors on AMD cards.
- A monitor picker (Display & FPS, Window) when you have more than one monitor. The game opens on the one you pick. `BB_DISPLAY` does the same from the command line: a number from `bb-gpu-capabilities.exe --displays` or part of the monitor's name. A monitor that is no longer connected falls back to the primary one.
- "Add to Steam" on the Play page adds `Bloodborne.exe` to your Steam library as a non-Steam game. Close Steam first; your `shortcuts.vdf` is backed up once as `shortcuts.vdf.bak`.
- The log starts with your GPU model, VRAM and driver, so bug reports carry them.
- Controllers whose sticks do not rest at the center (clone pads) work: each pad gets its own neutral point and an inner and outer dead zone. Connect the pad without touching the sticks; they read centered until its resting point is known.
- The online/offline screen at the title is skipped. `BB_SKIP_NETWORK_CHOICE=0` shows it again.

### Changed

- The "Beta versions" switch (Advanced, Launcher) that 1.6.17 added is on by default in a beta build, so a beta keeps offering the next one. Turned off, the launcher offers stable releases only.
- The FPS patch lists follow the ps4_cheats database of 2026-10-02 (ported by yumlevi): messengers and loading-screen pictures no longer replay their animations, and foliage wind stays smooth at high frame rates. A physics write that never did anything is gone. Menus keep the 240 FPS ceiling the older Uncap list had.
- An explicit `BB_RENDER_RES` above 1080p now gets the larger direct memory (`BB_DMEM_MB=9152`), in `run.py` and `run.sh`. Tested in code, not yet in the game.
- Mods on Windows use junctions and hard links, never symlinks (upstream #102). Not yet tested with a loose-file mod in the game. When the folder next to the game cannot be written, mods are prepared in `out` instead, where files are copied rather than linked on every launch. The launcher checks the free space first and says how many GB it copies.
- The setup scripts read and write text as UTF-8 and accept a plain ELF `eboot.bin`.
- The zip's `licenses` folder now also holds the AMD FidelityFX SDK and FSR-Vulkan licenses.

### Fixed

- "Close the launcher when the game starts" closed it after 5 seconds, which could kill the preparation of large mods (upstream #103). It now waits until the game window is up. The game writes `user\last_run.log` itself, so the log stays complete after the launcher closes.
- A crash or kill while the game saves can no longer leave a half-written save. Save files go to a temporary copy and replace the old file in one step. On Windows the game retries while another program holds the file. A leftover partial `.bbtmp` file recovers on the next start.
- Bad group counts in indirect GPU dispatches are set to zero before the dispatch runs (upstream #34).
- Old shader cache entries could pair with a different shader and trigger a Vulkan validation error (`VUID-VkGraphicsPipelineCreateInfo-layout-07988`) when the cache loaded. The game now drops each unusable entry instead of the whole cache. Your cache rebuilds once after you update. Whether this also explains the damaged cache entries some AMD players see at start is not confirmed; it needs an AMD log.
- Shell scripts keep LF line endings when you check out the source on Windows. `run.sh` used to stop at its second line.
- On a busy CPU, a shader compiled ahead of time could read code or texture and buffer descriptors the game had already reused, and its pipeline then broke Vulkan's rules (`VUID-VkGraphicsPipelineCreateInfo-layout-07988` and `-07990`). Such a compile is now thrown away and the shader is compiled again in order; the frame stats count them as dropped (3 in a 5-minute test run). The ahead-of-time compiles date from 1.6.4.

## [1.6.17] - 2026-10-09

### Added

- A "Beta versions" switch (Advanced, Launcher), off by default. With it on, the launcher also offers beta releases. The game files are the same as in 1.6.16; only the launcher changed. This release skipped the beta step: a beta could not reach players whose launcher had no switch yet.

## [1.6.16] - 2026-10-08

### Fixed

- Less stutter after loading an area, seen on AMD cards. This was a regression from 1.6.13. After the game left a malformed command packet in its command buffer, the thread that prepares draws ahead stopped reading, so every new shader compiled on the main thread for the rest of the session. Both threads now skip the packet.

## [1.6.15] - 2026-10-08

### Changed

- On AMD cards under Windows, object motion vectors stay off whatever the saved setting says. On an RX 6700 XT they cost frame rate with FSR 4, and frame generation drew some static objects wrong (vases, gravestones). NVIDIA cards keep them. The launcher explains this under the option (Advanced, Graphics). `BB_OBJECT_MOTION_AMD=1` turns them back on.

## [1.6.14] - 2026-10-08

### Changed

- Simple mode has no frame cap list. With frame generation on, the line under the switch says what you get, for example "Up to 120 game FPS, 240 on your 240 Hz screen".
- The Auto frame cap picks for you. With frame generation on it caps the game at half your screen's refresh rate (120 FPS at most), so frame generation never pauses. With frame generation off, or unable to run (upscaler off or HDR on), Auto applies no cap.
- Advanced mode lists plain caps (Auto, 60 FPS, 90 FPS and so on) with one line that explains your choice.

### Fixed

- The launcher window opens tall enough for everything on the page. On a short screen the banner shrinks first, then the page scrolls.

### Removed

- The "Half the refresh rate" frame cap from 1.6.13. Auto does the same.

## [1.6.13] - 2026-10-08

### Added

- A frame cap option, "Half the refresh rate (for frame generation)", that reads your main monitor's refresh rate. With frame generation on, the cap list shows what reaches the screen, for example "90 to 180 on screen".
- Crash reports in `last_run.log` name the thread and include a call stack.

### Changed

- The "Auto" frame cap said it capped at 120 but never applied a cap. The label now reads "Auto: no cap" and the behavior stays the same.

### Fixed

- Black character preview and missing loading-screen pictures. The texture cache checked a different part of the image when it marked it than when it refreshed it (yumlevi; three ports found the bug).
- The item picture on the loading screen went missing at outputs other than 1080p (bmy).
- Large monsters in Central Yharnam smeared under FSR and DLSS. Their motion vectors now cover bone palettes up to 64 KiB (bmy). `BB_MOTION_LARGE=0` turns this off.
- Crashes when loading an area: a missing user-data table (Mrsuss60), two draw-preparation threads patching the same instruction (Mrsuss60), and "Unimplemented PM4 type 0" (GoncaloLobo0).
- A crash when hashing game memory that can no longer be read (yumlevi, GoncaloLobo0).
- Memory corruption when the game splits a flexible memory mapping (bmy).
- FSR 4 no longer issues an invalid image barrier on the depth buffer, so its Vulkan validation errors are gone.
- A C++ exception while shaders are translated ahead of the GPU thread no longer closes the game. The GPU thread translates that draw instead.

## [1.6.12] - 2026-10-08

### Added

- FSR 3.1 frame generation on top of the upscaler you use (FSR 4, FSR 3.1, DLSS or XeSS). It shows one generated frame between every two game frames, so 60 FPS becomes about 120 on screen. It is off by default. Turn it on in the launcher or in the in-game menu (Insert, or L3+R3). It needs an upscaler.
- It works best from 60 FPS and adds a little input lag, because the real frame shows half a frame later. The HUD stays sharp: the generated frame comes from a copy of the scene without the HUD. It turns itself off in HDR and pauses while the game runs faster than half your screen's refresh rate. On AMD cards under Windows it works with vsync presentation, paced by the display.

### Changed

- The game asks Windows for a 1 ms timer resolution, so the wait between the generated and the real frame is precise.

### Fixed

- Checkboxes and sliders in the in-game menu keep your change again. With our compiler they always stored the old value (yumlevi).

## [1.6.11] - 2026-10-08

### Changed

- Object motion vectors work on AMD cards under Windows and are on by default. Testers with an RX 6700 XT and an RX 6600 played with them on and the game kept running. The crash came from the motion vertex shader reaching memory through raw pointers, which 1.6.10 replaced.
- Object motion is cheaper on every card. It writes each vertex position with one plain store instead of four atomic writes. On an RTX 4070 at the Hunter's Dream: 150 FPS with motion off, 123 with the old writes, 142 now.
- FSR 4.1.1 is not available on Windows. The Vulkan version of it needs an extension that no Windows driver is known to support yet. Before 1.6.5, picking it on Windows ran FSR 3.1 without telling you. Plain FSR 4 runs, RX 6000 cards included, where it costs more frames because they have no AI hardware.

### Removed

- The AMD test kit (`amd-motion-test`) is no longer in the package.

## [1.6.10] - 2026-10-08

Includes 1.6.9, a build for testers that changed nothing for other players.

### Added

- Test switches in the `amd-motion-test` kit that turn off one part of object motion at a time, so an AMD card can show which part its driver rejects (shipped in 1.6.9, removed in 1.6.11).

### Changed

- Object motion no longer reaches memory through raw pointers. Its vertex shaders read and write the motion buffers through normal storage-buffer bindings, on every graphics card. A 1.6.9 test on an RX 6700 XT found the cause of the AMD crash: with the pointer accesses switched off, object motion ran for minutes; with them on, the game lost the device as soon as a save loaded. On an RTX 4070 the motion vectors are identical and the validation layer shows no new errors. Object motion stays off on AMD until a tester confirms the build.

## [1.6.8] - 2026-10-08

### Fixed

- Two invalid Vulkan uses that appear when object motion vectors are on. NVIDIA's driver tolerates both and AMD's does not. The two extra varyings sat at locations 30 and 31, over the interface limit (`VUID-RuntimeSpirv-Location-06272`); they now use 26 and 27. Draws with motion vectors declared color target formats for slots with no image (`VUID-vkCmdDrawIndexed-dynamicRenderingUnusedAttachments-08912`); those slots are now empty. On an RTX 4070 both errors went from 20 to 0. These were real errors but not the cause of the AMD crash (found in 1.6.10). Object motion stays off on AMD.

## [1.6.7] - 2026-10-08

### Changed

- Pressing PLAY in `BLauncher.exe` also saves the game's output to `user\last_run.log` (or the saves folder you chose), as `Bloodborne.exe` already did.
- The AMD motion test kit finds the log in the saves folder set in the launcher, and tells you to press PLAY if the launcher opens instead of the game.

## [1.6.6] - 2026-10-08

### Added

- The `amd-motion-test` folder with `TEST-MOTION.bat`, a 5-minute object motion test for AMD players.

### Changed

- Every per-vertex access of object motion is bounds-checked and verified against its parameters, so a bad value cannot write outside its buffer. On AMD under Windows object motion stays off until AMD players confirm it works.

### Fixed

- Random crash to desktop since 1.6.4, with "GPU library assertion failed" (`Thread ID buffer addressing is not supported outside of compute` or `MapNumberConversion: Unreachable code`). The faster shader compilation from 1.6.4 had helper threads read a texture descriptor the game was still writing. The game now drops that attempt and translates the shader again in order. The same route crashed in about 1 run out of 5 before and 0 out of 6 after.

## [1.6.5] - 2026-10-08

### Added

- The Intel XeSS upscaler (SDK 3.0.2). It runs on AMD, Intel and NVIDIA cards and suits cards that cannot run FSR 4 well, such as the RX 6000 series or older. On an RTX 4070 at 1080p: XeSS 141 FPS, DLSS 148, FSR 3.1 159. XeSS frame generation needs DirectX 12, so this build leaves it out.
- The launcher checks your GPU at start and greys out the upscalers it cannot run, with the reason (DLSS needs an NVIDIA RTX card, for example). FSR 4 on RX 6000 cards is marked "may be slow". If your saved choice cannot run, the launcher switches to the best one available and tells you once.

## [1.6.4] - 2026-10-08

### Added

- The graphics driver's pipeline cache is saved in `user\cache` and reused at the next start. With a cold driver cache, preloading known shaders took 267 ms instead of 776 ms.

### Changed

- Shaders compile ahead of the frame. The draw-preparation workers, which already look at upcoming draws, now compile missing shaders there. On an RTX 4070 with every cache empty, over the opening cinematic and character creation, the time frames spent waiting on compiles fell from 1,912 ms to 301 ms, and the worst frame from 1.6 s to 0.54 s. `BB_ASYNC_COMPILE=0` brings back the old behavior.

## [1.6.3] - 2026-10-08

### Added

- ReShade 6.8.0, loaded for this game (nothing installs system-wide), with two presets: Natural (sharper detail, no color banding in fog and dark skies, a little more color and contrast) and Vivid (punchier color and contrast, a touch brighter, a light vignette). Cost on an RTX 4070: about 0.3 ms per frame at 1080p and 0.5 ms at 1440p.
- Simple mode has a "ReShade look" choice (Off, Natural, Vivid). Advanced, Graphics has the on/off switch, the preset list and a button for the presets folder. Press Home in the game to open ReShade's menu. Your changes and added presets survive updates.
- Effects that need the depth buffer (ambient occlusion, depth of field) are left out. The game has its own, and the upscaler hides depth from ReShade.

## [1.6.2] - 2026-10-08

### Added

- Install from PKG. In the launcher, pick your game, update and DLC `.pkg` files, or scan a folder to find them. The launcher extracts them, sets the game folder and turns on the DLC license. Extracting the full game takes 15 to 30 minutes.
- A new README with install steps, requirements and an FAQ.

### Changed

- Textures are freed against Windows' live VRAM budget, so cards with 6 to 8 GB run out of memory less often.
- On AMD cards under Windows, object motion vectors are off, to stop the "Device lost" crash with an upscaler on (upstream #39). `BB_OBJECT_MOTION_AMD=1` turns them back on.

### Fixed

- A crash when the player takes a hit on Intel 12th gen and newer CPUs (from Supermedo PR #2).
- A Windows file-open error, the texture cache stopping too soon, and setup on Chinese, Japanese or Korean Windows.

## [1.6.1] - 2026-10-08

### Changed

- `Bloodborne.exe` starts the game at once. The settings window is `BLauncher.exe`. The first run still opens the launcher so you can choose your game folder.

## [1.6] - 2026-10-08

First release of the fork.

### Added

- deadinside28's port, release 0.4, with its fixes (depth of field, a safer shader cache, camera motion vectors, minimizing no longer ends the game, and more).
- Supermedo's Windows port, v1.5: the launcher in 13 languages, NVIDIA DLSS on GeForce RTX cards, AMD FSR 3.1 and FSR 4.
- A redesigned launcher with a dark theme, animations, and a Simple mode that shows the settings a player needs. Advanced mode keeps every option.
- Support for The Old Hunters DLC license (experimental). With your DLC, run `setx BB_ADDCONT SPEXPANSIONDLC03` once in a command window, then start the game.

### Known limits

- Experimental: nobody has verified a full play-through.
- The new memory model of 0.4 runs on Linux alone, so Windows uses the previous model.

## Credits

This fork builds on [deadinside28's Bloodborne port](https://github.com/deadinside28/bloodborne_pc) (release 0.4; the unreleased work also takes fixes from its 0.5 pre-releases) and [Supermedo's Windows port](https://github.com/Supermedo/bloodborne_pc). Fixes also came from yumlevi, bmy, Mrsuss60 and GoncaloLobo0. JohnChen2727 filed the detailed AMD report in deadinside28/bloodborne_pc#39. The bundled Intel XeSS, NVIDIA DLSS, ReShade (crosire) and PkgTool (maxton's LibOrbisPkg) keep their own licenses, which are in the zip's `licenses` folder.

[Unreleased]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.16...develop
[1.6.16]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.15...windows-v1.6.16
[1.6.15]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.14...windows-v1.6.15
[1.6.14]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.13...windows-v1.6.14
[1.6.13]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.12...windows-v1.6.13
[1.6.12]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.11...windows-v1.6.12
[1.6.11]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.10...windows-v1.6.11
[1.6.10]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.8...windows-v1.6.10
[1.6.8]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.7...windows-v1.6.8
[1.6.7]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.6...windows-v1.6.7
[1.6.6]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.5...windows-v1.6.6
[1.6.5]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.4...windows-v1.6.5
[1.6.4]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.3...windows-v1.6.4
[1.6.3]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.2...windows-v1.6.3
[1.6.2]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6.1...windows-v1.6.2
[1.6.1]: https://github.com/0xCydral/bloodborne_pc/compare/windows-v1.6...windows-v1.6.1
[1.6]: https://github.com/0xCydral/bloodborne_pc/releases/tag/windows-v1.6
