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
- FSR 3.1 works on every GPU. FSR 4 needs its assets in fsr4_shaders\ (included in this
  package, or Graphics -> "Download FSR 4 assets"); GPUs without the required shader features
  fall back to FSR 3.1 by themselves.

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
- The full list of projects and patch authors is in README.md (Credits and licenses).
