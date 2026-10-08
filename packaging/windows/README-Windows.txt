Bloodborne (bbport) for Windows
===============================

No game files are included. You need your own decrypted dump of Bloodborne CUSA03173
(the folder with eboot.bin, sce_module, sce_sys, dvdroot_ps4), or the PlayStation 4 .pkg files
of the game (see "Installing from .pkg files"). Game version 1.09 is needed for the community
patches (60/90/unlocked FPS, resolution, effects); other versions run at 30 FPS.

Requirements
- Windows 10 (1903 or later) or Windows 11, 64-bit.
- A Vulkan 1.3 graphics card with a current driver.
- About 6 GB of free memory commit (RAM + page file), 10 GB for 1440p/4K output.
  Nothing else: Python and the libraries are inside this folder.

Starting
- BLauncher.exe opens the launcher. On "Game & effects" pick your game folder, adjust the
  settings, press PLAY. The "Play" page checks the game version, saves, graphics card and
  FSR 4 assets.
- Bloodborne.exe starts the game straight away with the settings saved in the launcher,
  without opening it. Set things up once in BLauncher.exe, then use Bloodborne.exe (or a
  shortcut to it, or add it to Steam with "Add a Non-Steam Game"). If no game folder is chosen
  yet it opens the launcher. BLauncher.exe --play does the same. The log goes to
  user\last_run.log.
- Installing from .pkg files: "Install from PKG..." (on the Play page, and on "Game & effects")
  opens one window. "Choose PKG files..." takes the game .pkg (about 31 GB), the v1.09 update
  .pkg and the DLC .pkg of Bloodborne; "Scan a folder..." looks for .pkg files in a folder and
  its subfolders (3 levels deep). Each file is listed with what it is (game, update version,
  DLC), and the best game, the highest update and the DLC are ticked for you; files that are
  not Bloodborne are listed with the reason and cannot be ticked. Both dialogs open in the
  folder you used last (else Downloads). "Install into" defaults to a "game" folder next to
  BLauncher.exe; about 1.1 times the size of the files must be free, and Install stays off
  until the space is there. The launcher extracts the game, applies the update over it, sets
  the game folder and runs the ready check. The game takes 15 to 30 minutes; the launcher
  stays usable and "Stop" cancels. A package PkgTool cannot read (encrypted, unsupported) is
  reported with PkgTool's own message. The DLC .pkg holds only the license:
  its label (SPEXPANSIONDLC03, The Old Hunters) is saved and reported to the game as BB_ADDCONT
  (Game & effects -> "DLC", with a Clear button). Only Bloodborne packages are accepted
  (CUSA03173, CUSA00900, CUSA00207, CUSA01363, CUSA03023); an update other than 01.09 is
  accepted with a warning. PkgTool needs the .NET Framework 4, which Windows 10 and 11 include.
- Advanced -> "Desktop shortcut" puts Bloodborne on the desktop.
- Updates: when a new version is out, the launcher shows it at the bottom left; "Update"
  downloads and installs it and opens the launcher again (saves, settings and mods are kept).
  Advanced -> "Check for updates" checks by hand.
- Advanced -> "Launcher language": English, Russian, Arabic, Spanish, Portuguese, French,
  German, Italian, Polish, Turkish, Chinese, Japanese, Korean (default: the Windows language).
- In the game, Insert (or L3+R3 on a gamepad) opens the port's menu (upscaler, resolution,
  effects). Keyboard: WASD move, arrows camera, Space Cross, Left Shift Circle, E Square,
  Q Triangle, 1/3 L1/R1, R/F L2/R2, Z/C L3/R3, I/K/J/L d-pad, Enter Options, Tab touchpad.

Data
- Saves and shader caches: user\ next to BLauncher.exe (the launcher can pick another folder).
- Settings: bbport.ini next to BLauncher.exe; launcher options in %APPDATA%\bbport-launcher.
- Generated files (prepared game image, patches): out\.

Cheats
- The "Cheats" page has cheats (never die, enemies do not see or hear you, Rally never fades,
  control the targeted enemy) and gameplay tweaks (no Rally, camera further away, no camera
  auto-rotation, run with less stick tilt, ragdoll physics). They are game patches for 1.09,
  applied when the game starts.

Problems
- Black screen at start: Advanced -> "Clear shader cache", then start again (the first minutes
  stutter while the cache is rebuilt).
- Send user\last_run.log with any bug report.

Upscaling
- DLSS: NVIDIA GeForce RTX 20 series or newer with a current driver (Graphics -> Upscaler ->
  DLSS, or the in-game menu). On other GPUs the option is greyed out.
- XeSS (Intel XeSS Super Resolution 2.0.2, from the XeSS SDK 3.0.2, bin\libxess.dll): for AMD, Intel
  and NVIDIA GPUs with DP4a support, a good choice where FSR 4 is slow or missing (for example
  Radeon RX 6000). It has no sharpener of its own: the Sharpness setting adds one.
- FSR 3.1 works on every GPU. FSR 4 needs its assets in fsr4_shaders\ (included in this
  package, or Graphics -> "Download FSR 4 assets"); GPUs without the required shader features
  fall back to FSR 3.1 by themselves.
- The launcher checks what your PC can run: upscalers it cannot are marked "not available" with the
  reason and cannot be picked. If the saved one cannot run, the launcher switches to DLSS (RTX
  GPUs) or FSR 3.1 and says so once. FSR 4 is allowed on Radeon RX 5000/6000 GPUs but marked "may
  be slow"; FSR 4.1.1 needs a Vulkan extension (VK_VALVE_shader_mixed_float_dot_product) that
  only Linux (Mesa) drivers offer, so it shows as not available on Windows drivers without it.

Frame generation (optional)
- Graphics -> "Frame generation (FSR 3.1)" (also on the Play page and in the in-game menu, or
  frame_generation=1 in bbport.ini, or BB_FRAME_GEN=1 for one run) shows one interpolated frame
  between every two game frames: 60 FPS of the game are shown as 120. It is AMD's FSR 3.1 frame
  interpolation and optical flow, driven by the game's depth and motion vectors, on top of the
  upscaler you picked (FSR 4, FSR 3.1, DLSS, XeSS or TAA). The HUD of the game frame is kept over
  the interpolated frame, so it does not smear. Default: off.
- It costs input lag: the game frame is shown about half a frame interval later than without
  it. Best with at least 60 FPS before generation; below that the interpolated frames show
  more artifacts and the lag is more noticeable.
- It is off, with a line in the log, when the upscaler is Off, with HDR output, and when the
  scene is rendered larger than the window. While the game runs faster than half of the
  display's refresh rate there is no room for a second image per frame: generation pauses
  ("Frame generation: idle, base FPS above half the refresh rate" in the log) and returns when
  the rate falls. For example on a 144 Hz display it works up to about 65 FPS of the game.
- Menus, loading screens and movies are shown as they are; generation restarts with the scene.
- With vertical sync (present mode Fifo, which AMD GPUs use on Windows; BB_PRESENT_MODE) the
  display paces the two images. With Mailbox or Immediate the port shows the interpolated frame
  and then the game frame half a frame interval later.
- The frame cap (Display & FPS) limits the game frames: a cap of 60 shows 120 images a second.
- The FPS counter reads "60 -> 120 FPS FG" while it works. With BB_FRAME_STATS=1 the log gets
  "Frame generation: N generated/s, M presented/s, base B FPS, X skipped" every 5 seconds.
- ReShade and other overlays see every shown image, so twice as many presents as game frames.

ReShade (optional)
- Graphics -> "ReShade" (or "ReShade look" on the Play page) switches it on for the next game
  start: two presets, Natural (sharpening, deband, a little contrast and colour) and Vivid (more
  contrast and colour, a light vignette). ReShade 6.8.0 and the effects are inside this package
  (bin\reshade\); nothing is installed on the system and nothing changes while it is off.
  Measured on an RTX 4070 it costs about 0.3 ms per frame at 1080p and 0.5 ms at 1440p, 3 to 4%
  of the frame at about 75 FPS. It does not read the game's depth, so there are no depth-based
  effects (the game has its own depth of field and ambient occlusion).
- In the game press Home to open ReShade's menu (Print Screen saves a screenshot to
  bin\reshade\screenshots\). Changes are saved in the preset in use.
- Your own presets: bin\reshade\presets\*.ini (create them in ReShade's menu, or copy .ini files
  there; "Open presets folder" opens it). They show up in the dropdowns. Updates keep
  bin\reshade\ReShade.ini and every preset already there, so your edits survive; delete a preset
  file to get the shipped one back with the next update. ReShade's log is bin\reshade\ReShade.log.
- It is a Vulkan layer enabled only for the game process (VK_ADD_LAYER_PATH, VK_INSTANCE_LAYERS and
  RESHADE_BASE_PATH_OVERRIDE are set by the launcher), so a ReShade installed on the system is not
  involved. Licences: licenses\ReShade-*.txt.

Mods and patches
- Put each mod in its own folder under mods\ (dvdroot_ps4\..., or chr\, parts\, ... directly);
  enable and order them on "Mods & patches". The game files are never changed. Without Windows
  Developer Mode the port links folders with junctions and files with hard links; when the game
  is on another drive, the temporary mod view is made next to the game folder.
- Third-party patches: shadPS4/GoldHEN XML files for 1.09 in patches\.

Credits
- bbport (the Linux port this is built on): https://github.com/deadinside28/bloodborne_pc
- Windows port: https://github.com/Supermedo/bloodborne_pc
- PkgTool, which extracts the .pkg files: maxton/LibOrbisPkg v0.2, LGPL-3.0, unmodified, in
  bin\pkgtool\ (license: licenses\PkgTool-LICENSE.txt, source: https://github.com/maxton/LibOrbisPkg).
- ReShade, which the optional post-processing uses: crosire/reshade 6.8.0, BSD-3-Clause, unmodified,
  in bin\reshade\ (https://reshade.me). Effects by CeeJay.dk (SweetFX, MIT), AMD (FidelityFX CAS, MIT)
  and haasn/JPulowski (Deband, MIT); licenses: licenses\ReShade-*.txt.
- The full list of projects and patch authors is in README.md (Credits and licenses).
