<div align="center">

<h1><img src="docs/banner.svg" alt="Bloodborne PC: a blood moon over Gothic rooftops, native on Windows from your own PS4 copy" width="720"></h1>

**Play Bloodborne natively on Windows 10 and 11, from your own PS4 copy. No emulator.**

[English](README.md) · [Русский](docs/original-readme/README.ru.md)

<a href="LICENSE"><img src="https://img.shields.io/badge/License-GPL--2.0-blue?style=for-the-badge" alt="License GPL-2.0"></a>
<img src="https://img.shields.io/badge/Platform-Windows%2010%2F11-0078D6?style=for-the-badge&logo=windows&logoColor=white" alt="Platform Windows 10/11">
<a href="https://github.com/0xCydral/bloodborne_pc/releases/latest"><img src="https://img.shields.io/github/v/release/0xCydral/bloodborne_pc?style=for-the-badge&logo=github&logoColor=white&label=Release" alt="Latest release"></a>
<a href="https://github.com/0xCydral/bloodborne_pc/releases"><img src="https://img.shields.io/github/downloads/0xCydral/bloodborne_pc/total?style=for-the-badge&logo=github&logoColor=white&label=Downloads" alt="Total downloads"></a>
<img src="https://img.shields.io/badge/Status-Experimental-orange?style=for-the-badge" alt="Status: experimental">
<a href="https://discord.gg/KYZRKk9CB"><img src="https://img.shields.io/badge/Discord-Original%20project-5865F2?style=for-the-badge&logo=discord&logoColor=white" alt="Discord of the original project"></a>

![The launcher in Simple mode](docs/screenshots/launcher-simple.png)

</div>

> [!WARNING]
> Bloodborne PC is experimental. The game boots, loads saves and plays with sound, gamepad and saving, but a full play-through has not been verified. **No game files are included.** You need your own copy of the game.

> [!NOTE]
> This project is not related to shadPS4. Please ask questions about it on the
> [original project's Discord server](https://discord.gg/KYZRKk9CB), not on the shadPS4 server.

**Players:** [What this is](#what-this-is) · [Status](#status) · [How it works](#how-it-works) · [What you need](#what-you-need) · [Install](#install) · [Update](#update) · [First launch and key settings](#first-launch-and-key-settings) · [Controls](#controls) · [Troubleshooting and FAQ](#troubleshooting-and-faq) · [Contributing](#contributing) · [Legal](#legal)

**Developers:** [For developers](#for-developers)

## What this is

Bloodborne PC lets you play *Bloodborne* for PlayStation 4 on a Windows 10 or 11 PC, using your own copy of the game. It is not an emulator. The game's own code runs on your processor, and a graphics layer turns the PS4's drawing commands into Vulkan, the graphics interface your video card driver provides.

An emulator translates every instruction of the game while it runs. Bloodborne PC does not: the PS4 has an ordinary x86-64 processor, the same kind of chip as your PC, so the game's code runs on it as it is. What the game cannot do alone is talk to the PS4 operating system, and the port supplies its own version of those system libraries.

You set everything up in a small program called the launcher: pick your game, pick a few options, press **PLAY**. You can play with a controller or with a keyboard and mouse.

> [!IMPORTANT]
> **No game files are included.** You need your own copy of Bloodborne for PS4 (CUSA03173, version 1.09), either as a game folder or as the PS4 `.pkg` files. This project is not affiliated with Sony Interactive Entertainment, FromSoftware, AMD or NVIDIA. See [Legal](#legal).

## Status

**Experimental, playable.** The Hunter's Dream and several areas of Yharnam were played with it. A full play-through has not been verified.

| Area | State | Notes |
|---|---|---|
| Boot, saves, sound, saving | **Works** | The game boots, loads saves and plays with sound and saving. |
| Gamepad, keyboard and mouse | **Works** | Both can be used at the same time. See [Controls](#controls). |
| Upscalers (DLSS, FSR 3.1, FSR 4, XeSS, TAA) | **Works** | Which ones your card can run is shown in the launcher. See [Upscaler](#upscaler). |
| Frame generation | **Works** | AMD frame interpolation on top of any upscaler, in the Windows build since 1.6.12. Off by default. |
| Install from `.pkg` files | **Works** | The launcher installs the game, the update and the DLC license. |
| A full play-through | **Not verified** | |
| The Old Hunters DLC | **Experimental** | The license is reported to the game. See the [FAQ](#troubleshooting-and-faq). |
| FSR 4.1.1 on Windows | **Experimental** | Off until you opt in. Not in the zip. |
| Linux | **Upstream** | The Linux version is in the [upstream project](https://github.com/deadinside28/bloodborne_pc). |

### Tested hardware

| Graphics card | What was checked |
|---|---|
| NVIDIA GeForce RTX 5080 | FSR 3.1 and FSR 4, game versions 1.00 and 1.09 |
| NVIDIA GeForce RTX 4070 | FSR 4.1.1 through fsr4vk, and frame generation for five minutes at a 60 FPS base |
| AMD Radeon RX 6700 XT, RX 6600 | The fix for the crash when the world loads (see the [FAQ](#troubleshooting-and-faq)). The RX 6600 also ran FSR 4.1.1 through fsr4vk. |

Cards that are not on this list are untested here. If you play on one, a [bug report](https://github.com/0xCydral/bloodborne_pc/issues/new?template=bug_report.yml) with your `user\last_run.log` helps either way.

## How it works

<p align="center"><img src="docs/how-it-works.svg" alt="Your own Bloodborne copy goes through the launcher, a prepared game image and the loader. The loader feeds the PS4 runtime and the Vulkan renderer, and together they run Bloodborne on your CPU." width="860"></p>

The step labels in the picture match the numbers below.

1. **Prepare.** The launcher saves your settings in `bbport.ini` and prepares the game. Scripts convert the game's own executable (`eboot.bin`) into a memory image with the PS4 libc and Fios2 libraries linked in, compile the community patches (frame rate, resolution, effects) and set up your mods as an overlay. Your game folder is not changed.
2. **Load.** A small loader maps the image into memory and jumps into the game. The game's code runs on your processor, with no emulation and no instruction translation.
3. **Answer system calls.** Every call from the game into a PS4 system library lands in the port's own runtime, which implements only what Bloodborne calls: memory, threads, files, audio, controller and saves.
4. **Draw.** The game's graphics commands go to a Vulkan renderer. It is built on the video core and shader recompiler of [shadPS4](https://github.com/shadps4-emu/shadPS4), the PS4 emulator, and adds the upscalers and frame generation.

The details are under [Internals](#internals) in the developer half.

## What you need

| | |
|---|---|
| **Windows** | Windows 10 (1903 or later) or Windows 11, 64-bit |
| **Graphics card** | A card with Vulkan 1.3 and a current driver. DLSS needs an NVIDIA GeForce RTX 20 series or newer. |
| **Memory** | About 6 GB of free memory (RAM plus page file). 10 GB for 1440p or 4K output. |
| **The game** | Your own copy of Bloodborne CUSA03173, version 1.09: a decrypted game folder, or the PS4 `.pkg` files |
| **Disk space** | To install from `.pkg` files, about 1.1 times the size of the files must be free (the game package is about 31 GB). |

Version 1.09 is the one to use. The community patches (60, 90 and unlocked FPS, resolution, effects) are made for it, and other versions run at 30 FPS.

The game folder is the one with `eboot.bin`, `sce_module`, `sce_sys` and `dvdroot_ps4` inside.

You do not need a compiler, Python or an emulator. Everything else is in the zip.

## Install

**[Download the latest release](https://github.com/0xCydral/bloodborne_pc/releases/latest)** (`bbport-windows.zip`) · **[Discord of the original project](https://discord.gg/KYZRKk9CB)**

1. Download `bbport-windows.zip` and unzip it anywhere.
2. Open `Bloodborne.exe`. The first time, it opens the launcher.
3. Tell the launcher where your game is. Press **Browse...** and choose the game folder, the one with `eboot.bin` in it. If you have `.pkg` files instead, press **Install from PKG...** (see below).
4. Press **PLAY**.

After that, `Bloodborne.exe` starts the game straight away with your saved settings. Open `BLauncher.exe` whenever you want to change settings.

| File | What it does |
|---|---|
| `Bloodborne.exe` | Starts the game with your saved settings. The first time it opens the launcher. |
| `BLauncher.exe` | The settings launcher. `BLauncher.exe --play` starts the game like `Bloodborne.exe`. |
| `user\` | Your saves and shader caches, next to the executables. The launcher can pick another folder. |

<details>
<summary><b>Installing from .pkg files</b></summary>

**Install from PKG...** is on the Play page and on **Game & effects**. It opens one window.

1. Press **Choose PKG files...** and pick the game `.pkg` (about 31 GB), the version 1.09 update `.pkg` and the DLC `.pkg` of Bloodborne. Or press **Scan a folder...** to look for `.pkg` files in a folder and its subfolders (3 levels deep). Both dialogs open in the folder you used last, or in Downloads.
2. The window lists each file with what it is (game, update version, DLC) and ticks the best game, the highest update and the DLC for you. Files that are not Bloodborne are listed with the reason and cannot be ticked.
3. Check **Install into**. It defaults to a `game` folder next to `BLauncher.exe`. **Install** stays off until about 1.1 times the size of the files is free.
4. Press **Install**. The launcher extracts the game, applies the update over it, sets the game folder and runs the ready check. The game takes 15 to 30 minutes. The launcher stays usable, and **Stop** cancels.

The launcher accepts Bloodborne packages (CUSA03173, CUSA00900, CUSA00207, CUSA01363, CUSA03023) and nothing else. An update other than 01.09 is accepted with a warning. A package that PkgTool cannot read (encrypted, unsupported) is reported with PkgTool's own message. PkgTool needs the .NET Framework 4, which Windows 10 and 11 include.

The DLC `.pkg` holds nothing but the license. Its label (`SPEXPANSIONDLC03`, The Old Hunters) is saved and shown under **Game & effects > DLC**, which also has a **Clear** button.

</details>

## Update

When you open the launcher, it checks for a new version. A card at the top right says so. **Update** downloads and installs the new version and opens the launcher again. Your saves, settings and mods are kept.

To check by hand, use **Advanced > Check for updates**. The launcher offers stable releases. Beta releases are tested less. The launcher offers them when **Advanced > Beta versions** is on (from 1.6.17). A beta build starts with that switch on.

## First launch and key settings

The launcher opens in the **Simple** view (the screenshot at the top of this page), with the settings most players need. The **Advanced** switch at the top right adds tabs: Graphics, Display & FPS, Controls, Game & effects, Cheats, Mods & patches, Advanced and Log.

The **Ready check** box on the Play page tells you if the game version is right (01.09), where your saves go, which graphics card was found and whether the FSR 4 files are there.

Here is the Advanced view on the Graphics tab:

![The launcher in Advanced view on the Graphics tab, with the upscaler, preset and output size](docs/screenshots/launcher-advanced.png)

The in-game menu changes some of the same settings while you play: upscaler, preset, sharpness, output resolution and game effects. Open it with **Insert** (or **L3+R3** on a controller; Numpad 0 with Num Lock off also works).

### Upscaler

An upscaler draws the game at a lower resolution and then enlarges the picture to your screen size. You get more frames per second for a small loss of sharpness. Pick one in **Upscaler**:

| Your graphics card | What to pick |
|---|---|
| NVIDIA GeForce RTX 20 series or newer | **DLSS**. It needs a current driver. |
| AMD Radeon RX 7000 and newer | **FSR 4** |
| AMD Radeon RX 6000 and older | **XeSS** or **FSR 3.1**. FSR 4 runs on Radeon RX 5000 and 6000 too, but it costs more frames there. |
| NVIDIA cards without DLSS | **FSR 3.1**, which works on every card, or **XeSS** on cards with DP4a support |
| Intel | **FSR 3.1** or **XeSS**. FSR 4 runs where the card has the shader features it needs. |
| Any card | **TAA** draws at your screen's resolution with anti-aliasing and no upscaling. |

You can leave the default. The launcher checks your card when it opens and greys out the upscalers it cannot run, with the reason. If your saved choice cannot run, the launcher switches to DLSS (RTX cards) or FSR 3.1 and tells you once. FSR 4 needs its files in `fsr4_shaders\` (they are in the zip, or **Graphics > Download FSR 4 assets**). Without them, or on a card that lacks the shader features, the game falls back to FSR 3.1 by itself.

### Preset and output

**Output** is the size of the picture the game gives your screen, from 1280 x 720 (the Steam Deck) to 3840 x 2160 (4K). 1440p and 4K need about 10 GB of free memory.

**Preset** says how much lower than the output size the game draws before the upscaler enlarges it: Native AA (x1.0), Quality (x1.5), Balanced (x1.7), Performance (x2) and Ultra Performance (x3). The number is the scale on each side. A lower-quality preset gives more FPS.

In the in-game menu, a change of output or preset can ask you to **Apply and restart the game**. The **Live resolution changes** setting (Graphics tab, Auto by default) lets a strong graphics card switch without a restart, at a cost in frame rate.

### Frame rate

**Unlocked** lets the game run as fast as your PC allows. **60 FPS**, **90 FPS** and **30 FPS (as on PS4)** fix the rate instead. Above about 120 FPS the game's movement slows down, which is a limit of the game itself, so keep the cap at 120 or lower. **Advanced > Display & FPS** has a frame cap list: Auto, 60, 90, 120, 144, 165, 240 (these three are marked with a warning) or No limit. **Auto** applies no cap, unless frame generation is on (see below).

### Frame generation

Frame generation shows an extra, computed picture between every two real frames. A game running at 60 FPS appears as 120 on your screen, and the HUD stays sharp. It is AMD's frame interpolation, and it works on top of any upscaler (FSR 4, FSR 4.1.1, FSR 3.1, DLSS, XeSS or TAA). Picking it does not change your upscaler. It is off by default. Turn it on with the **Frame generation** switch in Simple view, in **Graphics**, or in the in-game menu.

It costs a little input lag, because the real frame appears about half a frame later. It works best when the game already runs at 60 FPS or more. With frame generation on, the launcher caps the game at half of your screen's refresh rate (at most 120 FPS), so every game frame has room for its extra picture. Simple view shows the result, for example "Up to 72 game FPS, 144 on your 144 Hz screen." On a screen under 100 Hz it helps little. It switches itself off when the upscaler is Off or HDR output is on.

### Other settings

- **Fullscreen**, **Game language** (English, Russian, Japanese, French, Spanish, German or Italian) and **Launcher language** (English, Arabic, Russian, Spanish, Portuguese, French, German, Italian, Polish, Turkish, Chinese, Japanese or Korean; the default is your Windows language).
- **ReShade look** (Off, Natural or Vivid) changes colors, contrast and sharpness on top of the game. ReShade is inside the zip, and nothing is installed on your system. Press **Home** in the game to open its menu.
- **Cheats** page: never die, enemies do not see or hear you, Rally never fades, control the targeted enemy. It also has gameplay tweaks: camera distance, no camera auto-rotation, running with less stick tilt and ragdoll physics.
- **Game & effects** page: turn the game's own effects on or off. You can also do this in the in-game menu.
- **Mods & patches** page: turn mods and third-party patches on and off, and set their order. The game files are never changed.
- **Advanced > Desktop shortcut** puts Bloodborne on your desktop. The **Add to Steam** button on the Play page adds `Bloodborne.exe` to your Steam library (close Steam first).
- **Display & FPS > Monitor** (shown when you have more than one monitor) picks the monitor the game opens on.

## Controls

The default keys follow the Dark Souls III layout. Controller and keyboard work at the same time. Remap either one in the launcher under **Controls**, a tab of the Advanced view.

| Keyboard and mouse | PS4 button or action |
|---|---|
| W A S D | Move |
| Mouse, I J K L | Camera |
| Space | Circle (dodge, hold to dash, back) |
| E / Enter | Cross (interact, confirm) |
| Left button / Shift + left button | R1 / R2 |
| Right button / Shift + right button or Left Ctrl | L1 / L2 |
| R / F | Square (quick item) / Triangle (blood vial) |
| Q or wheel click / C | R3 (lock on) / L3 (jump) |
| Up, wheel up / Down, wheel down | D-pad up / down |
| Left, Shift + wheel down / Right, Shift + wheel up | D-pad left / right |
| Esc / Tab or G / Backspace | Options (menu: equipment, items) / left touchpad (gestures) / right touchpad |

There is no walk input, so Left Alt (walk in Dark Souls III) is not bound.

![The launcher's Controls tab: mouse settings and the button assignments list](docs/screenshots/launcher-controls.png)

The mouse turns the camera while the game window has focus and the port's menu is closed. Press **Insert** or **Alt+Tab** to let go of it. **Controls > Mouse** has the on and off switch, the sensitivity, *Invert vertical look* and *Camera turns only by the mouse while walking*.

To change keys, open **Controls > Button assignments**. Each input has **Change** and **Add** for keys, mouse buttons and the wheel, and a drop-down for the controller button. Hold Shift, Ctrl or Alt first to make a combination such as Shift + left button. The **Dark Souls III layout** button asks first, then sets the keyboard and mouse back to the defaults. If several controllers are connected, **Controls > Controller** picks one.

The game draws PlayStation buttons. **Controls > Button icons** changes them. *Automatic* (the default) shows Xbox icons for an Xbox or other controller, PlayStation icons for a PlayStation one, and keys and mouse icons when no controller is connected. The launcher redraws the icons from your own copy of the game, and nothing in the game folder changes.

You type the character name on the keyboard, in a box over the game.

## Troubleshooting and FAQ

> [!TIP]
> For any bug report, attach `user\last_run.log` (next to `BLauncher.exe`, or in the saves folder you chose). Say which graphics card you use and what happened. The log starts with your graphics card model, memory and driver.

<details>
<summary><b>The launcher says it cannot find my game.</b></summary>

The Ready check shows "No eboot.bin in the game folder". Press **Browse...** and choose the folder that has `eboot.bin` in it, next to `sce_module`, `sce_sys` and `dvdroot_ps4`. If you have `.pkg` files, use **Install from PKG...** instead. `Bloodborne.exe` opens the launcher instead of the game until a game folder is set.

</details>

<details>
<summary><b>Which game version do I need?</b></summary>

Version 1.09 (CUSA03173). Other versions run at 30 FPS on Windows, because the community patches are made for 1.09. If you have the update as a separate folder, copy it over the base game and replace the files. The launcher checks the executable and says what is missing.

</details>

<details>
<summary><b>The game stays on a black screen.</b></summary>

Open the launcher and use **Advanced > Clear shader cache**, then start again. The first minutes stutter while the cache is rebuilt.

</details>

<details>
<summary><b>The game crashes when the world loads on an AMD card.</b></summary>

Update from the launcher first. The earlier cause, object motion vectors, was fixed in 1.6.10, and an RX 6700 XT and an RX 6600 confirmed it (upstream [issue #39](https://github.com/deadinside28/bloodborne_pc/issues/39)). Since 1.6.15, object motion vectors stay off on AMD cards under Windows. Check that **Advanced > Experimental features** and the AMD object motion vectors switch in the Graphics tab are off. If it still crashes, send `user\last_run.log` as described above.

</details>

<details>
<summary><b>The game's movement slows down at a high frame rate.</b></summary>

Above about 120 FPS the movement slows down. This is a limit of the game itself. Keep the frame cap at 120 or lower.

</details>

<details>
<summary><b>An upscaler is greyed out, or the game picked another one.</b></summary>

The launcher checked your graphics card and the option cannot run on it, and it says why. DLSS needs an NVIDIA GeForce RTX 20 series or newer. FSR 3.1 works on every card. FSR 4 needs its files in `fsr4_shaders\` (in the zip, or **Graphics > Download FSR 4 assets**). A card that lacks the shader features falls back to FSR 3.1 by itself.

</details>

<details>
<summary><b>How do I use FSR 4.1.1 on Windows?</b></summary>

It is experimental, off until you opt in, and not in the zip. The built-in FSR 4.1.1 needs a Vulkan extension that no Windows driver offers yet. On Windows the game can load [fsr4vk](https://github.com/dvj5411/fsr4vk) instead, a separate program that runs the same AMD model. It was checked on a GeForce RTX 4070 and a Radeon RX 6600.

1. **Advanced > Performance**: turn on **Experimental features**.
2. **Graphics > FSR 4.1.1 (experimental)**: press **Download FSR 4.1.1** (about 14 MB, once).
3. **Graphics > Upscaler**: choose **FSR 4.1.1 (experimental)**.

If you downloaded it with an earlier version, the same place offers **Update FSR 4.1.1**. The new build makes it work on AMD cards. The download goes to the `fsr4vk` folder next to `BLauncher.exe`, and launcher updates leave that folder alone.

</details>

<details>
<summary><b>Frame generation does not seem to run.</b></summary>

It is off by default, and it switches itself off when the upscaler is Off or HDR output is on. While the game runs faster than half of your screen's refresh rate, there is no room for the extra picture, so it pauses and returns when the rate falls. Set **Frame cap** to Auto and the launcher picks a cap that fits your screen. On a 144 Hz screen, for example, it works up to about 65 FPS of the game.

</details>

<details>
<summary><b>The mouse does not turn the camera, or I cannot reach my desktop.</b></summary>

The mouse turns the camera while the game window has focus and the port's menu is closed. Press **Insert** or **Alt+Tab** to let go of the mouse. On game version 1.09 the camera follows the mouse with no stick lag. On another version the mouse acts as the right stick. Check **Controls > Mouse** if it is switched off.

</details>

<details>
<summary><b>Where are my saves and settings?</b></summary>

Saves and shader caches are in `user\` next to `BLauncher.exe`. Settings are in `bbport.ini` next to it, and launcher options are in `%APPDATA%\bbport-launcher`. Generated files (the prepared game image and patches) are in `out\`. Updates keep your saves, settings and mods.

</details>

<details>
<summary><b>Does The Old Hunters DLC work?</b></summary>

Support for the DLC license is experimental. The areas of The Old Hunters ship with the v1.09 data, and the port reports the license to the game. Installing the DLC `.pkg` with **Install from PKG...** turns it on: the launcher saves the license label, and it shows under **Game & effects > DLC**. Without that `.pkg`, set the environment variable `BB_ADDCONT=SPEXPANSIONDLC03` yourself: run `setx BB_ADDCONT SPEXPANSIONDLC03` once in a command window, then start the game.

</details>

<details>
<summary><b>The character preview on the character creation screen is empty.</b></summary>

This was fixed in 1.6.13. Update from the launcher. If you still see it on a current version, report it with your `user\last_run.log`. The character itself is created as normal.

</details>

<details>
<summary><b>How do I use mods and patches?</b></summary>

Put each mod in its own folder under `mods\` (`dvdroot_ps4\...`, or `chr\`, `parts\` and so on placed straight in the mod folder). Enable them and set their order on **Mods & patches**. The game files are never changed. Third-party patches are shadPS4 or GoldHEN XML files for 1.09 in `patches\`. See [mods and patches](docs/MODS.md).

</details>

<details>
<summary><b>How do I put Bloodborne on the desktop or in Steam?</b></summary>

Use **Advanced > Desktop shortcut**, or point a shortcut or Steam (*Add a Non-Steam Game*) at `Bloodborne.exe`. The **Add to Steam** button on the Play page does the Steam part for you. Set things up once in `BLauncher.exe` first. If no game folder is chosen yet, `Bloodborne.exe` opens the launcher.

</details>

<details>
<summary><b>Where can I get the game?</b></summary>

Nowhere here. The zip contains no game files and the launcher does not download them. Use a copy you own, dumped from your own console, as a game folder or as `.pkg` files.

</details>

Still stuck? Ask on the original project's [Discord server](https://discord.gg/KYZRKk9CB) or open an [issue](https://github.com/0xCydral/bloodborne_pc/issues). If the Discord invite has expired, the [original project's README](https://github.com/deadinside28/bloodborne_pc#readme) has the current one.

## Contributing

The most useful help is a good bug report. Use the [bug report form](https://github.com/0xCydral/bloodborne_pc/issues/new?template=bug_report.yml) and attach `user\last_run.log`. Reports from graphics cards that are not in [Tested hardware](#tested-hardware) are welcome.

For code changes, read [CONTRIBUTING.md](CONTRIBUTING.md). In short: keep a pull request to one change, add or update a test, use Conventional Commits, and never include game files.

## Legal

Bloodborne PC is not affiliated with, endorsed by or connected to Sony Interactive Entertainment, FromSoftware, AMD or NVIDIA. "PlayStation", "PS4" and "Bloodborne" belong to their owners. NVIDIA, GeForce RTX and DLSS are trademarks of NVIDIA Corporation.

No game files are included or provided with Bloodborne PC. Use it only with a copy of the game you own and have dumped yourself. You are responsible for following the laws that apply to you.

Bloodborne PC is licensed under the **GNU GPL, version 2 or later** ([LICENSE](LICENSE)). It contains code from shadPS4 (GPL-2.0-or-later). Other components keep their own licenses. The full list is under [Credits and licenses](#credits-and-licenses).

---

## For developers

Everything below is reference material: what this fork adds to the projects it is built on, how the port works, how to build it, its settings, and its credits. Players do not need it.

The Linux version of the port is in the upstream project. The game's own x86-64 code runs on the CPU, and its graphics are translated to Vulkan by a renderer derived from [shadPS4](https://github.com/shadps4-emu/shadPS4).

[What this fork combines](#what-this-fork-combines) · [Upscalers and GPUs](#upscalers-and-gpus) · [Controls reference](#controls-reference) · [Internals](#internals) · [Building from source](#building-from-source) · [Settings and environment variables](#settings-and-environment-variables) · [Project layout and roadmap](#project-layout-and-roadmap) · [Credits and licenses](#credits-and-licenses)

### What this fork combines

This fork brings together the work of several people and adds a few things of its own.

| Source | What it brings |
|---|---|
| [deadinside28](https://github.com/deadinside28/bloodborne_pc), port release 0.4 | The original project: the native Linux port and the renderer work behind it |
| [Supermedo](https://github.com/Supermedo/bloodborne_pc), Windows port v1.5 | The Windows port, a launcher in 13 languages, NVIDIA DLSS (RTX 20 series and newer), AMD FSR 3.1 and FSR 4 |
| This fork | Support for *The Old Hunters* DLC license (experimental) |
| This fork | A redesigned launcher with Simple and Advanced modes and animations |
| This fork | Install from PKG: the launcher installs the game from your PS4 `.pkg` files (base game, update and DLC) |
| This fork | Frame generation: AMD FidelityFX frame interpolation on top of any upscaler (FSR 4, FSR 3.1, DLSS or XeSS), about twice the frames on screen from a 60 FPS base, with the HUD kept sharp |
| This fork | Intel XeSS (SDK 3.0.2) as an upscaler for AMD, Intel and NVIDIA GPUs with DP4a support, and a launcher that greys out the upscalers a GPU cannot run, with the reason |
| This fork | ReShade 6.8 with two Bloodborne presets (Natural and Vivid), switched on from the launcher |
| This fork | Shaders compiled ahead of the frame on the draw-preparation threads, and the driver's pipeline cache saved between runs: fewer first-time stutters |
| This fork | Object motion vectors on AMD GPUs under Windows: their driver lost the device on the raw-pointer stores, so the motion buffers are now storage-buffer bindings (upstream [issue #39](https://github.com/deadinside28/bloodborne_pc/issues/39)) |
| This fork | A fix for the crash when you are attacked on Intel 12th gen and newer CPUs (red-zone protection, from Supermedo [PR #2](https://github.com/Supermedo/bloodborne_pc/pull/2)) |
| This fork | A live VRAM budget on Windows |

### Upscalers and GPUs

| Upscaler | GPUs | Notes |
|---|---|---|
| **DLSS** | NVIDIA GeForce RTX 20 series and newer | Needs a current driver. Greyed out on other GPUs. |
| **FSR 3.1** | Every Vulkan 1.3 GPU | The fallback for everything else. |
| **FSR 4** (INT8, model v07) | GPUs with the required Vulkan shader features, RDNA2 and RDNA3 included | The assets are in the Windows package. GPUs without the features fall back to FSR 3.1 by themselves. |
| **FSR 4.1.1** | INT8 on any GPU with the required shader features, FP8 on AMD RDNA4 (RX 9000) | Linux: built from your own AMD DLL. See [FSR 4.1.1](#fsr-411-from-your-own-dll). Windows (experimental): through the fsr4vk provider, see [below](#fsr-411-on-windows-experimental). |
| **XeSS** (Intel XeSS Super Resolution 2.0.2, from the XeSS SDK 3.0.2, `bin\libxess.dll`) | AMD, Intel and NVIDIA GPUs with DP4a support | A good choice where FSR 4 is slow or missing, for example Radeon RX 6000. It has no sharpener of its own: the Sharpness setting adds one. |
| **TAA** | Every GPU | Native-resolution temporal AA. Works without an FSR model. |

| GPU | What to know |
|---|---|
| **NVIDIA** | DLSS on RTX 20 series and newer. The *New memory and translation model* is unavailable: the driver cannot map the game's memory as needed. |
| **AMD** | FSR 3.1 and FSR 4. The only GPUs where the experimental *New memory and translation model* works. On Windows, object motion is off by default for stability (an experimental switch turns it on). Some AMD cards still crash when the game world loads. |
| **Intel** | FSR 3.1 and TAA, plus FSR 4 where the GPU has the shader features. The *New memory and translation model* is untested. |

### Controls reference

The player view of the controls is [above](#controls). These are the details.

**Mouse.** While the game window has focus and the port's menu is closed, the mouse turns the camera (game version 1.09; on another version it acts as the right stick, and the log says which one is in use). **Insert** (the port's menu) or **Alt+Tab** lets go of the mouse. In the launcher, **Controls > Mouse** has the switch, the sensitivity, *Invert vertical look* and *Camera turns only by the mouse while walking* (the game then stops turning the camera by itself as the character walks; off by default so a gamepad keeps its camera). Mouse buttons and the wheel are inputs like keys: **Change** and **Add** take a click or a wheel step, and holding Shift, Ctrl or Alt first makes a combination such as Shift + left button.

The **Dark Souls III layout** button (Controls > Button assignments) asks, then sets the keyboard and mouse bindings back to those defaults and leaves the gamepad bindings alone; *Reset all* does the same for every setting.

**Button icons.** The game draws PlayStation buttons. *Controls > Button icons* (`button_icons` in `bbport.ini`) changes them: *Automatic* (the default) picks Xbox icons for a connected Xbox or other controller, PlayStation icons for a PlayStation one, and keycaps and mouse icons when none is connected; *PlayStation*, *Xbox* and *Keyboard and mouse* force a set. The keyboard icons show the first key bound to each input (Space as "Spc", the left stick as WASD, a mouse button as a mouse with that button lit). The icons are chosen when the game starts, so changing controllers in the game keeps them until the next start. They are redrawn from your own copy of the game (`menu/common.tpf.dcx`) into `out/icons` next to the launcher, which the game reads instead of its own file; nothing in the game folder changes and the port ships no game art. A mod with its own `menu/common.tpf.dcx` keeps priority, and the log says the icons are off for that run. The big prompt images baked into the menu atlases (for example the circle, cross and L3 in the item pickup prompt) are redrawn too.

The settings are lines of `bbport.ini`: `mouse_camera`, `mouse_sensitivity` (0.022 degrees of turn per mouse count x the value, 0.01 to 20), `mouse_invert_y`, `mouse_no_auto_rotation`. A binding line takes `Mouse Left`, `Mouse Right`, `Mouse Middle`, `Mouse X1`, `Mouse X2`, `Wheel Up`, `Wheel Down`, each optionally after `Shift+`, `Ctrl+` or `Alt+` (for example `key.r2=Shift+Mouse Left`).

### Internals

The plain-words version is [How it works](#how-it-works) above. These notes go deeper.

<details>
<summary><b>Overview: Wine and DXVK for one game</b></summary>

bbport is the counterpart of Wine + DXVK for a single game: *Bloodborne* for PlayStation 4 (CUSA03173, game version 1.09). The game's original executable runs directly on the PC.

- **CPU.** The PS4 CPU is x86-64, so the game's code runs directly on the PC's CPU, with no emulation and no instruction translation.
- **System libraries.** As Wine replaces the Windows API, the bbport runtime (`src/runtime_*.c`) implements exactly the PS4 OS functions Bloodborne calls: memory, threads, files, audio, pad, saves.
- **Memory.** In the new mode, as in a PC game: the game's data in system RAM, VRAM used the way a PC game uses it. The mode is still experimental and runs on AMD GPUs only. Without it, the game runs on the old memory model, as in 0.3.
- **Graphics.** The graphics are translated to Vulkan by a renderer derived from shadPS4 and heavily extended for this game, including temporal upscaling with AMD FSR 3.1, FSR 4 and FSR 4.1.1. The PS4 GPU's command stream (PM4) is a recording of the game's graphics API calls: it is written by 99 functions of the statically linked libGnm, so decoding the stream and translating the calls themselves come to the same thing. GCN shaders are translated to SPIR-V. In the new mode the command processor's work (memory writes, DMA, fences) is translated into Vulkan commands as well.

One part still works the old way: the translator reads resource descriptors (textures, buffers) from the game's memory on the CPU, and recognises textures by address. The next big step is the GPU reading the descriptors itself (bindless), with textures as objects created at load time.

Two steps remain to the full Wine + DXVK model: make the new mode the default, and move the reading of resource descriptors from the CPU to the GPU.

"Port" here means a build for this one game, not a rewrite of its source code, which the project neither has nor includes.

</details>

<details>
<summary><b>Highlights</b></summary>

- **Native execution.** The eboot is converted offline into a flat memory image. PS4 libc and libSceFios2 are linked into it as native code. No CPU emulation and no per-instruction translation: the game code runs at full speed.
- **Two modes** (launcher > *Mode*):
  - **Switch off (the default), as in 0.3.** The old memory model: VRAM copies of the game's memory, writes tracked through page protection. It includes every fix made since 0.3 (motion vectors and the upscaler, flicker with DoF on, a damaged shader cache).
  - **New memory and translation model (experimental, AMD GPUs only).** The game's memory lives in system RAM and the GPU reads it where it is, as a PC game's buffers. Data it reads often is kept in VRAM and given back when unused (textures after 20 s, buffers after 60 s). There is no write tracking and no copy of the whole GPU-visible memory. The PS4 command processor's work is translated into Vulkan commands rather than emulated on the CPU: memory writes (`WRITE_DATA`, DMA) are done by the GPU in command-stream order, and fences are written once the GPU has really finished the work. Where the game is CPU-bound it runs 20 to 25% faster (measured standing in the Hunter's Dream), with fewer stutters. **It may crash**, and has been tested thoroughly only on the author's PC (RX 7800 XT). On NVIDIA and Intel the switch is unavailable: NVIDIA's driver cannot map the game's memory as needed, and Intel is untested. Without the launcher, use `BB_PC_MODEL=1`. To try it on another GPU, use `BB_PC_MODEL_ANY_GPU=1`.

  Unused textures are freed in both modes, so VRAM no longer grows with every area visited.
- **Unlocked frame rate.** Community patches (`patches/Bloodborne.xml`) make the simulation use the real frame time. Measured: about 90 FPS at 4K with FSR 4 Balanced on an RX 7800 XT, about 150 FPS at 1440p with FSR 4 Quality. 30, 60 and 90 FPS modes exist too.
- **Temporal upscaling built for this game.** Bloodborne has no velocity buffer, so bbport computes motion vectors itself: camera motion from depth and the scene matrices, and object motion (characters, cloth, weapons) from the vertex positions of the previous frame. The scene is jittered sub-pixel (Halton) and rendered at a reduced resolution. The upscaler fills the output (720p for the Steam Deck, 1080p, 1440p or 2160p), and the UI is drawn natively at the output resolution.
  - **FSR 3.1** (FireBurn/FSR-Vulkan).
  - **FSR 4 (INT8, model v07)** on GPUs exposing the required Vulkan shader features, RDNA2 and RDNA3 included (see [Linux requirements](#linux-requirements)).
  - **FSR 4.1.1**: AMD's 4.1.1 DLL is recorded once under vkd3d-proton and its passes are replayed natively on Vulkan. The output is **bit-exact** with the DLL. Two variants, as in the DLL: INT8 on any GPU with the required shader features, and FP8 matrices on RDNA4 (RX 9000, picked automatically). The assets are built on your machine from one DLL of your own (4.1.x): the launcher's *FSR 4.1.1 from your own AMD DLL > Choose DLL...* button (2 to 5 minutes, twice that on RDNA4, needs a recent Proton), or `tools/fsr4cap`.
  - Faster than AMD's own shaders on RDNA3: the final passes of FSR 4 and 4.1.1 were rewritten to store through workgroup memory (3.5x and 2.3x faster, bit-exact). FSR 4 costs about 4 ms at 4K on an RX 7800 XT instead of about 6 ms.
- **Multi-threaded GPU command processing.** The PS4 command stream is decoded on one thread and draws are bound and recorded on another (two-stage pipeline), with a Vulkan recording thread and helper threads for memory copies. Early on the single GPU thread capped the game at about 26 FPS. Now it runs at 90 to 150 FPS depending on resolution and scene.
- **In-game menu** (Insert or L3+R3): upscaler, preset, sharpness, output resolution, game effects (chromatic aberration, DoF, motion blur, SSAO, the game's own AA, SSR, model LOD). It opens where it was left, with the mouse cursor shown over it.
- **GTK4 launcher** and an **AppImage** for the Steam Deck (Linux).

</details>

<details>
<summary><b>How it differs from shadPS4</b></summary>

| | shadPS4 | bbport |
|---|---|---|
| Scope | General PS4 emulator, many games | One game: Bloodborne v1.09 |
| Loading | Its own ELF loader and kernel emulation at run time | The eboot is converted offline (`scripts/`) into an image with PS4 libc/Fios2 linked in. A C loader maps it and jumps into the game (loader and runtime: about 5k lines) |
| Memory | The GPU's view of PS4 memory is kept in VRAM copies, synchronized through page-protection write tracking | By default the same model (as in 0.3). In the *New memory and translation model* mode (AMD): the game's memory in system RAM, used by the GPU in place, frequently read data in VRAM, freed when unused |
| Command processor | Emulated: memory writes, DMA and fences are done by the CPU while decoding | By default the same. In the new mode translated into Vulkan commands that the GPU runs in stream order, fences written after the work has really finished |
| System libraries | Broad HLE of the PS4 OS | A small runtime (`src/runtime_*.c`) that implements exactly what Bloodborne calls: memory, threads, sync, files, audio (incl. ATRAC9), pad, saves, AppContent |
| GPU | shadPS4 video core and shader recompiler | The same core (vendored, GPL) with about 200 marked changes (`bbport:`) plus new modules: two-stage draw pipeline, render-state and texture-set memoization, render-scale proxies, motion vectors, FSR 3.1/4/4.1.1, frame capture and GPU profiler |
| GPU thread | One thread processes the whole command stream (the bottleneck in Bloodborne) | Decode and draw recording run on separate threads. The work scales with the hardware threads (Steam Deck included) |
| Upscaling | None | Temporal (FSR 3.1, FSR 4, FSR 4.1.1) with the game's own motion vectors and jitter |
| Game patches | Patch files applied by the emulator | The same community patches, compiled at start (`scripts/patches.py`). Render resolution, effects and FPS from the launcher |

Without shadPS4 there would be no bbport: its renderer and shader recompiler are the base of the graphics side.

</details>

<details>
<summary><b>Resolution, presets and TAA</b></summary>

**Resolution and preset changes.** For outputs other than 1080p (720p on the Steam Deck, 1440p, 4K) the whole game renders at the preset's resolution, set by a patch at start. This is the fastest path. Changing the output or the preset in the in-game menu then needs *Apply and restart the game*. The *Live resolution changes* setting (launcher, in-game menu, `bbport.ini` `live_resolution=0|1|auto`; **off by default**, but the Windows launcher starts at `auto`) instead keeps the game at 1080p internally and scales its render targets at run time, so 720p, 1080p, 1440p and 4K and the presets switch without a restart. It costs more: the game then believes it renders 1080p and draws more (for example about 8 times more small lights), and some targets are copied between sizes. Use it on strong desktop GPUs only (`auto` turns it on for discrete GPUs with 8+ GB that are not pre-Turing NVIDIA). 1080p output and TAA always use the live path.

**TAA.** A separate native-resolution temporal AA mode in the launcher and overlay, switchable live without an FSR model. The saved FSR preset is restored when returning to FSR. FSR Native AA adds reconstruction on top of full-resolution rendering and can be slower than disabling AA. TAA also adds work compared with no temporal AA. The RCAS switch and the 0 to 2 sharpness control also work with TAA. Sharpening runs after temporal accumulation and leaves its history and HUD unchanged.

</details>

<details>
<summary><b>Mods, free camera and the game debug menu</b></summary>

**Mods.** The launcher accepts separate loose-file mod folders (with `dvdroot_ps4/`, an extra wrapper folder, or the game folders such as `chr/` directly; file name case does not matter), with enable switches and load order. A sibling `CUSA03173-mods/` overlay also works. The original game is preserved, and later mods override conflicting files. Without Windows Developer Mode, the port links folders with junctions and files with hard links. When the game is on another drive, the temporary mod view is made next to the game folder.

**Third-party patches.** shadPS4-format XML patch files in the data directory's `patches/`, switched on and off in the launcher. See [mods and patches](docs/MODS.md).

**Launcher language.** The Windows launcher has 13 languages (see [First launch and key settings](#first-launch-and-key-settings)). The Linux GTK4 launcher offers Russian, English or Brazilian Portuguese and follows the system language by default.

**Free camera and game debug menu** (v1.09). Enable the corresponding switches in the launcher or in-game menu and restart. Free camera uses Lance McDonald's [GoldHEN patch](https://github.com/GoldHEN/GoldHEN_Patch_Repository/blob/main/patches/xml/Bloodborne-Orbis.xml). Hold Cross and press L3 to cycle modes (keyboard: hold E and press C). It needs no fonts and conflicts with *Enemy Control*.

For the game debug menu, install `DbgFont14h.ccm` and `DbgFont14h.tpf` from [Debug Menu and XML Patch](https://www.nexusmods.com/bloodborne/mods/253) into the game's `dvdroot_ps4/font/` first. Startup rejects missing or empty font files instead of launching the unsafe patch. Open it with the left touchpad / Tab (with the debug menu on, the left half no longer opens the gestures). Backspace is the right touchpad. Touch coordinates are forwarded from SDL gamepads, and Back/Select emulates a left click on pads without a touch surface. The port's settings menu remains Insert / L3+R3.

The touchpad: its left half (Tab, Back/Select) opens the gestures, the right half (Backspace) the key items.

GPU occlusion queries still use synthetic pixel counters (`PixelPipeStatDump`), and `IT_SET_PREDICATION` is unimplemented. Free camera allows visual investigation. It does not implement GPU occlusion culling.

</details>

### Building from source

Players do not need this section. The Windows package is built from this repository.

#### Windows

Build in an MSYS2 CLANG64 shell (Windows 10 1903+ or 11, 64-bit):

```bash
bash build.sh                           # builds the loader and the GPU library
bash packaging/windows/package.sh       # makes a self-contained folder with Bloodborne.exe
bash packaging/windows/build_dlss.sh    # builds the DLSS bridge, separately
```

You can also run from source with `python run.py` or the Tkinter launcher (`launcher/bbport_launcher_win.py`). Players of the packaged build need no Python. Details and the differences from Linux (TLS, guest memory, exceptions) are in [packaging/windows/README.md](packaging/windows/README.md).

#### Linux

<a id="linux-requirements"></a>

<details>
<summary><b>Requirements</b></summary>

- Linux x86-64, a Vulkan 1.3 GPU. Tested: AMD RX 7800 XT with Mesa 26 (RADV). The *New memory and translation model* mode needs an AMD GPU.
- FSR 4 and 4.1.1 require shader Float16, Int8/Int16, integer dot products, linear compute derivatives and extended storage image formats. FSR 4.1.1 additionally requires `VK_VALVE_shader_mixed_float_dot_product`. Unsupported choices fall back to FSR 3.1 before the first frame and are disabled in the in-game menu.
- Your decrypted game dump: the `CUSA03173` folder (eboot.bin, sce_module, ...), version 1.09. A dumped update is a separate folder: copy it over the base game, replacing files. The base game alone (1.00) crashes at start (guest offset 0x20348b8). The launcher and `run.sh` check the executable and say what is missing (`BB_SKIP_GAME_CHECK=1` skips the check).
- To build: GCC, CMake, Ninja, Python 3, glslang, SDL3, Vulkan headers and the libraries in `shell.nix`. With [Nix](https://nixos.org) everything comes from `shell.nix` automatically.

</details>

<details>
<summary><b>Build and run</b></summary>

```bash
git clone --recursive https://github.com/deadinside28/bloodborne_pc.git bbport && cd bbport
bash build.sh                        # builds out/bb-probe and out/gpu/libbbgpu.so
BB_GAME_DIR=/path/to/CUSA03173 bash run.sh
```

Or use the launcher (pick the game folder, settings, *Start*):

```bash
bash launcher/bb-launcher.sh         # launcher/install-desktop.sh adds it to the app menu
```

By default the game folder is expected next to the repository (`../CUSA03173`). Saves and the shader cache go to `user/` (the launcher lets you choose another folder), and settings to `bbport.ini`. A gamepad is used through SDL3 (the launcher's *Controls > Controller* picks one when several are connected; `BB_GAMEPAD=<GUID or part of the name>`). The keyboard and the mouse work too, also next to a connected gamepad (the Steam Deck always has one). All are remapped in the launcher (*Controls*).

</details>

<details>
<summary><b>Upscaler assets</b></summary>

Upscaler assets are not included. FSR 3.1 needs none.

```bash
bash tools/fetch_fsr4_assets.sh      # FSR 4 v07 (MIT, built from AMD's source by Q2RTX)
# FSR 4.1.1, from your own AMD DLLs (e.g. OptiScaler's FSR4_LATEST), needs GE-Proton 10 or newer:
bash tools/fsr4cap/build_assets.sh <amd_fidelityfx_upscaler_dx12.dll>
```

<a id="fsr-411-on-windows-experimental"></a>

**FSR 4.1.1 on Windows (experimental).** No Windows driver offers `VK_VALVE_shader_mixed_float_dot_product`, which the replay above needs. The port can instead load [fsr4vk](https://github.com/dvj5411/fsr4vk) (GPLv3, one author, about a month old), a Vulkan FFX provider that runs AMD's INT8 4.1.1 model with standard Vulkan features. The port does not include or link it: `tools/fetch_fsr4vk.py` downloads fsr4vk v0.4.3 with one patch (about 14 MB, SHA-256 checked) from this port's GitHub release and puts `amd_fidelityfx_upscaler_vk.dll` into `fsr4vk\` next to the executable (or `BB_FSR4VK_DIR`). Upstream's v0.4.3 fails on AMD's Windows driver ([issue #1](https://github.com/dvj5411/fsr4vk/issues/1)); the patch drops the variable descriptor count flag from its three heap bindings, and the patched build works on an RX 6600 and an RTX 4070. The GPLv3 source of the patched build is the release's `fsr4vk-v0.4.3-amdfix-src.zip`, and `fsr4vk\SOURCE.txt` and `PATCH.md` say what changed. If the launcher finds the original upstream DLL in that folder, the FSR 4.1.1 card offers *Update FSR 4.1.1*; the game uses the DLL that is there until you update. With the DLL present, the *FSR 4.1.1* choice (`BB_UPSCALER=fsr411`) works on a GPU that has mutable descriptor types, descriptor buffers and the usual FSR 4 INT8 features; the extra device features are requested only then. Any failure falls back to FSR 3.1 with one log line, and `bb-gpu-capabilities --upscalers` says why fsr411 is unavailable. fsr4vk's author tested Linux/Proton only; it has no RCAS (the port's own sharpening pass covers it), and at an odd output size it leaves the last row or column unwritten. Checked here on one RTX 4070 (driver 616.56) at 1920x1080 from 1280x720 (Quality), in the Hunter's Dream: the upscaler pass costs about 2.4 ms of GPU time per frame, against 2.0 ms for FSR 4 (v07) and 0.55 ms for FSR 3.1, and frame generation ran for five minutes at 60 FPS base without a device loss. Measured once, not a benchmark.

<a id="fsr-411-from-your-own-dll"></a>

**FSR 4.1.1 from your own DLL.** The easiest way is the launcher: *Upscaler > FSR 4.1.1 from your own AMD DLL > Choose DLL...*, then pick `amd_fidelityfx_upscaler_dx12.dll` version 4.1.x, from OptiScaler's `FSR4_LATEST` folder or from a game with FSR 4.1. That one file is enough: AMD's DLL exports the FidelityFX API itself, so no loader is needed. Only a DLL without these exports would be recorded through `amd_fidelityfx_loader_dx12.dll`, and the launcher would ask for it.

The DLL is checked at once, without a long capture. These do not fit: AMD's official FSR 4.0.x (for example Pragmata's 4.0.3, which AMD enables on RDNA4 only, and under Proton it does not start on other GPUs) and community builds (4.0.2b, 4.1.1b and similar OptiScaler INT8 builds: another model and pass count). Then the DLL runs under Proton and is recorded at 20 sizes (progress in the row and in the *Log* tab; on RDNA4 a second time, for the FP8 variant), the passes are translated to SPIR-V, and FSR 4.1.1 is selected.

The Proton build is picked automatically: the first under which the DLL enables FSR 4.1. That means GE-Proton 10 or newer, Proton-CachyOS, or Proton Experimental (tested: GE-Proton 11, Proton-CachyOS 11, Experimental of October 2026; Steam's Proton 11.0 and GE-Proton 9 do not work). It runs in the Steam runtime it requires (Steam installs it the first time any game runs with that Proton), or through umu-launcher when installed. On NixOS umu-launcher comes from `nix-shell` (downloaded the first time, about 1.7 GB). The AppImage runs the recording on the system itself, through the user's systemd (`systemd-run --user`), since the system's `/nix` is out of its sight. Recording on the Steam Deck is not verified yet. If the DLL does not enable FSR 4.1 there, build on a PC and copy the `fsr4_411` folder. The same from the command line: the launcher's (and the AppImage's) `--build-fsr411 <DLL>`.

From source the script takes its tools from the system (MinGW GCC, CMake, Ninja, Python 3, SPIRV-Tools, Git) or from Nix. The AppImage has them prebuilt.

**FP8 on RDNA4.** When vkd3d-proton offers FP8 cooperative matrices (RDNA4), the DLL runs another variant of its passes: other shaders and weights, other dispatch sizes. The build records both, INT8 into `fsr4_411/` and FP8 into `fsr4_411/fp8/`, and the game picks FP8 itself when the GPU has FP8 matrices (`VK_EXT_shader_float8`). The log says `Upscaler: FSR 4.1.1 replay, FP8 ...`. `BB_FSR411_VARIANT=int8` forces INT8. Checked on an RX 7800 XT through vkd3d-proton's FP16 emulation of FP8 (`BB_FSR4CAP_FP8=1` records it there too, `fp8emu/`): bit-exact with the DLL. **Not yet run on RDNA4 hardware.**

</details>

<details>
<summary><b>AppImage and Steam Deck</b></summary>

`bash build.sh && bash packaging/appimage.sh` produces `dist/Bloodborne-bbport-x86_64.AppImage`. Data goes in `~/.local/share/bbport`, and `--play` starts the game without the launcher window (Game Mode). FSR 4.1.1 models are not packaged: build them with the launcher's button (see above). They go to `~/.local/share/bbport/fsr4_411` (`BB_PACKAGE_FSR411=1` bundles a local `fsr4_411` into an AppImage for your own devices). On the Steam Deck pick the 1280x720 output (the game is 16:9, and on the 1280x800 screen it gets thin bars).

**Adding the AppImage to Steam** (*Add a Non-Steam Game*) needs no options, and the compatibility tool does not matter. Steam preloads its overlay into every non-Steam game. The AppImage removes it before its own programs start, so the Steam overlay is not shown in the game. Where Steam runs games without FUSE (NixOS: Steam's FHS sandbox, where the AppImage exits with *Cannot mount AppImage*), set the launch options to:

```
TMPDIR=$HOME/.cache APPIMAGE_EXTRACT_AND_RUN=1 NO_CLEANUP=1 %command%
```

The AppImage then unpacks itself (about 2 GB, `~/.cache/appimage_extracted_*`) on the first start (about 10 s) and reuses that copy afterwards. A new AppImage version gets a new copy, and the old one can be deleted. Without `TMPDIR` it would unpack into Steam's `/tmp`, which is in RAM there. Add ` --play` after `%command%` to skip the launcher.

**NVIDIA in the AppImage.** Startup discovers the host's installed 64-bit NVIDIA Vulkan ICD and exposes its vendor libraries alongside the bundled AMD/Intel drivers. This keeps the NVIDIA userspace driver matched to the host kernel module. Standard Linux distributions keep these libraries under `/usr/lib*`. On NixOS the AppImage's internal `/nix/store` may hide them. In that case copy the NVIDIA libraries into an accessible directory and set `BB_NVIDIA_LIB_DIR=/path/to/libraries` (the NVIDIA manifest must also be accessible). Explicit `VK_DRIVER_FILES` and `VK_ICD_FILENAMES` overrides are preserved. Diagnose drivers inside the package with:

```bash
./Bloodborne-bbport-x86_64.AppImage --vulkan-info 2>&1 | tee bbport-vulkan.log
```

A user reported successful startup with FSR 3 on a GTX 1060 6GB (Fedora 44, NVIDIA 580.178.04). Selecting FSR 4 caused a black window. Use FSR 3 on this configuration.

**MangoHud** is bundled in the AppImage. Enable its checkbox in the launcher. If MangoHud is also installed system-wide, or Steam's performance overlay is on (Steam Deck game mode), only one overlay is drawn (two drew doubled, offset text). When running from source, install MangoHud separately. A diagnostic launch with `VK_LOADER_LAYERS_DISABLE=~implicit~` also disables MangoHud.

</details>

### Settings and environment variables

Most players never need these. Everything here is also reachable from the launcher or the in-game menu where it makes sense.

<details>
<summary><b>Environment variables</b></summary>

| Variable | Meaning |
|---|---|
| `BB_GAME_DIR=<path>` | Game folder (Linux `run.sh`) |
| `BB_SKIP_GAME_CHECK=1` | Skip the check of the game executable |
| `BB_PC_MODEL=1` | The new memory and translation model (AMD only). `0`, the old model as in 0.3, is the default. |
| `BB_PC_MODEL_ANY_GPU=1` | Try the new model on a GPU other than AMD |
| `BB_FRAME_STATS=1` | Frame statistics, including a `Memory:` line: VRAM, GTT, RSS, images and guest blocks in VRAM |
| `BB_ANISO=N` | Anisotropic filtering of scene textures. 16 by default, 0 = the game's own. |
| `BB_GC_IDLE_SECONDS=N`, `BB_VRAM_IDLE_SECONDS=N` | How long unused textures and buffers stay in VRAM (20 and 60) |
| `BB_GC_BUDGET_MB=N` | Texture cache budget, as on integrated GPUs |
| `BB_BREADCRUMBS=0` | No GPU breadcrumbs. With them, a GPU hang names the draw or dispatch it is stuck in. |
| `BB_GPU_PROFILE=1` | GPU time per pass |
| `BB_FSR4_PROFILE=1` | GPU time per FSR 4 pass |
| `BB_UPSCALER=taa\|fsr3\|fsr4\|fsr411\|off\|none` | Choose the upscaler |
| `BB_FSR411_VARIANT=int8\|fp8\|fp8emu` | FSR 4.1.1 variant. By default FP8 where the GPU has FP8 matrices. |
| `BB_FSR4VK_DIR=<folder>` | Windows: the folder holding fsr4vk's `amd_fidelityfx_upscaler_vk.dll` (default: `fsr4vk` next to the executable) |
| `BB_FSR4VK=0` | Windows: do not use fsr4vk even when its DLL is installed |
| `BB_FRAMES_AHEAD=N` | How many frames the GPU command thread may run ahead of the GPU. 1 by default, 0 = unbounded. |
| `BB_PRESENT_THREAD=0` | Present on the vblank thread, as before |
| `BB_LIVE_RES=1` | Live resolution changes instead of the startup patch for outputs other than 1080p |
| `BB_READBACKS=0\|1\|2` | Reads of GPU-written memory by the game. 1 by default, 2 precise and slow, 0 off. |
| `BB_PAD_RECORD=file`, `BB_PAD_REPLAY=file` | Record a route with F9, replay it in scripted tests |
| `BB_PRESENT_DUMP_TRIGGER=file`, `BB_PRESENT_DUMP_COUNT=N` | Dump N consecutive presented frames |
| `BB_ADDCONT=SPEXPANSIONDLC03` | Add-on licenses reported to the game, comma-separated. This one is The Old Hunters, whose areas ship with the v1.09 data. Experimental. |
| `BB_GAMEPAD=<GUID or part of the name>` | Pick a controller |
| `BB_ICONS_DIR=<folder>` | A mod layer with a generated `dvdroot_ps4/menu/common.tpf.dcx` (the button icons), put under the mods by `run.py`. The launcher sets it at Play; a mod with its own file takes priority. |
| `BB_PAD_DEADZONE=N`, `BB_PAD_DEADZONE_OUTER=N` | Stick dead zone: inner (5) and outer (127) limit, 0..127 |
| `BB_PAD_CENTER=lx,ly,rx,ry`, `BB_PAD_CENTER_CAL=0` | Stick neutral of a pad that rests off-centre, in SDL units (-32768..32767); `BB_PAD_CENTER_CAL=0` turns the automatic measurement off. The neutral is measured each time the pad connects: if a stick drifts at rest, reconnect the pad without touching the sticks |
| `BB_DISPLAY=<number or part of the name>` | Monitor for the window and fullscreen: its number as `bb-gpu-capabilities --displays` lists it (1, 2, ...) or part of its name, case-insensitive. Empty, or none matches: the primary monitor, with a `Display:` line in the log. |
| `BB_INDIRECT_GUARD=0`, `BB_INDIRECT_GUARD_MAX=N` | Indirect dispatches with garbage group counts are skipped (on by default); `0` runs them as the game wrote them; `N` is the most groups in all (4194304) |
| `BB_SAVE_TRACE=1` | Log every operation on save files (`/savedataN`) |
| `BB_PACKAGE_FSR411=1` | Bundle a local `fsr4_411` into an AppImage |
| `BB_FSR4CAP_FP8=1` | Record the FP8 emulation variant (`fp8emu/`) |
| `BB_NVIDIA_LIB_DIR=<path>` | NVIDIA libraries for the AppImage on NixOS |

More in [docs/](docs). Recent changes: [docs/CHANGES_0.4.md](docs/CHANGES_0.4.md) (in Russian) and [docs/CHANGES_2026-10-06.md](docs/CHANGES_2026-10-06.md).

</details>

### Project layout and roadmap

<details>
<summary><b>Repository layout</b></summary>

| Path | Contents |
|---|---|
| `src/` | Loader (`probe.c`) and the HLE runtime |
| `scripts/` | Offline preparation of the game image, module linking, patch compiler |
| `gpu/` | Renderer library: vendored shadPS4 video core with this port's changes (`gpu/VENDOR.txt`), shims, ImGui menu, FSR 4.1.1 runtime (`gpu/shadps4/video_core/renderer_vulkan/fsr411`) |
| `launcher/`, `packaging/` | GTK4 launcher. Nix package and AppImage. Windows launcher (`bbport_launcher_win.py`, translations in `bbport_lang.py`) and package (`packaging/windows/`) |
| `patches/` | Community patches for Bloodborne |
| `tools/` | Developer tools: scripted runs, A/B toggles, FSR benchmark helpers, FSR 4 shader rewrites, `fsr4cap` (FSR 4.1.1 recording and extraction) |
| `tests/` | Loader, runtime, patch and renderer tests |
| `docs/` | Design notes and measurements ([upscaler](docs/upscaler.md), [parallel GPU](docs/parallel_gpu.md), [motion vectors](docs/motion_vectors.md), [roadmap](docs/ROADMAP.md)) |

Tests: `bash build.sh --test`, `python3 -m unittest discover -s tests`, and `ninja -C out/gpu motion-history-test cache-consistency-test ui-composition-test scene-resolution-test motion-shader-test settings-test`.

</details>

<details>
<summary><b>Roadmap</b></summary>

- The new memory and translation model on by default once it is stable, NVIDIA included. The old model is removed after that.
- The GPU reads resource descriptors itself (bindless), textures are objects created at load time: no shader code or constant engine executed on the CPU, no texture cache keyed by address.
- Shaders translated ahead of time, at install, not during play.
- More CPU parallelism in GPU command processing (split the draw-recording stage further), scaling to all hardware threads. This matters most for the Steam Deck.
- Async compute for the upscaler (the frame is GPU-bound at 4K).
- XeFG frame generation through a Wine helper sharing Vulkan memory (a memory-bridge prototype is in `tools/bridge_helper`). Inputs exposed so that OptiScaler-style mapping works. DLSS and XeSS super resolution already run in the Windows build (XeSS frame generation needs DirectX 12 and is left out there).
- Reactive and transparency masks for particles and fog. Frame generation (AMD frame interpolation) is in the Windows build since 1.6.12.
- Fix the races in AMD's FSR 4.1.1 shaders at output widths that are not multiples of 64 (for example 1600x900), as already done for the left-edge race in FSR 4 v07 at 1080p.
- Steam Deck validation of the AppImage, and HDR output.

</details>

### Credits and licenses

Bloodborne PC is licensed under the **GNU GPL v2 or later** ([LICENSE](LICENSE)). It contains code from shadPS4 (GPL-2.0-or-later). Third-party components keep their licenses.

The Linux port and almost all of the work behind it are by [deadinside28](https://github.com/deadinside28/bloodborne_pc). The Windows port is by [Supermedo](https://github.com/Supermedo) (Mohammed Albarghouthi).

| Component | License and use |
|---|---|
| [shadPS4](https://github.com/shadps4-emu/shadPS4) | GPL-2.0+. Video core and shader recompiler |
| [sirit](https://github.com/shadps4-emu/sirit), [half](https://half.sourceforge.net/) | Vendored libraries |
| [FSR-Vulkan](https://github.com/FireBurn/FSR-Vulkan) by FireBurn | MIT. FSR 3.1 on Vulkan and the FSR 4 v07 provider |
| AMD FidelityFX SDK | MIT |
| [LibAtrac9](https://github.com/Thealexbarney/LibAtrac9) | MIT |
| [Dear ImGui](https://github.com/ocornut/imgui) | MIT |
| DejaVu fonts | |
| [dxil-spirv](https://github.com/HansKristian-Work/dxil-spirv) | MIT. Used to build the FSR 4.1.1 assets |

Game patches are by Kyo, Lance McDonald, auser1337, illusion, emoose and other community members (`patches/Bloodborne.xml`). AMD's FSR 4 DLLs and model data are not distributed here.

<details>
<summary><b>Windows build components</b></summary>

The Windows build also uses: [magic_enum](https://github.com/Neargye/magic_enum) (MIT), [miniz](https://github.com/richgel999/miniz) (MIT), [xbyak](https://github.com/herumi/xbyak) (BSD-3-Clause), [SDL3](https://github.com/libsdl-org/SDL) (zlib), [FFmpeg](https://ffmpeg.org), [Vulkan Memory Allocator](https://github.com/GPUOpen-LibrariesAndSDKs/VulkanMemoryAllocator) (MIT), [glslang](https://github.com/KhronosGroup/glslang), [SPIRV-Tools](https://github.com/KhronosGroup/SPIRV-Tools), [fmt](https://github.com/fmtlib/fmt), [Boost](https://www.boost.org), [robin-map](https://github.com/Tessil/robin-map), [xxHash](https://github.com/Cyan4973/xxHash), [Zydis](https://github.com/zyantific/zydis), all built with [MSYS2](https://www.msys2.org) CLANG64 ([LLVM/Clang](https://llvm.org), libc++, mingw-w64 winpthreads).

The launcher is frozen with [PyInstaller](https://pyinstaller.org) and the icon is drawn with [Pillow](https://python-pillow.org). FSR 4 assets for the in-launcher download come from [FireBurn/Q2RTX](https://github.com/FireBurn/Q2RTX). The Windows guest memory layout follows the placeholder approach of shadPS4's Windows memory manager.

DLSS runs through `bbport_dlss.dll` (`gpu/dlss_bridge`, MIT), adapted from the DLSS bridge of [IFreemz/shadPS4-Bloodborne-DLSS-FSR](https://github.com/IFreemz/shadPS4-Bloodborne-DLSS-FSR) and built against the [NVIDIA DLSS SDK](https://github.com/NVIDIA/DLSS). NVIDIA's `nvngx_dlss.dll` is redistributed under its license (`licenses/NVIDIA-DLSS-LICENSE.txt` in the package). NVIDIA, GeForce RTX and DLSS are trademarks of NVIDIA Corporation. The port itself contains no NVIDIA code.

The Bloodborne-style icon is original artwork, not taken from the game.

</details>

<div align="center">

[Releases](https://github.com/0xCydral/bloodborne_pc/releases) · [Discord of the original project](https://discord.gg/KYZRKk9CB) · [Original project](https://github.com/deadinside28/bloodborne_pc) · [Windows port](https://github.com/Supermedo/bloodborne_pc)

</div>
