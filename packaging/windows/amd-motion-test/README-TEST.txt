Bloodborne PC: AMD motion test
==============================

Why this test exists
--------------------
On some AMD graphics cards the game stops with "Device lost" a few seconds after a save loads, when the
"object motion" feature is on. To keep the game stable, that feature is switched off on AMD cards by default.
We changed how the feature reaches memory, and we need your card to confirm it no longer crashes.

What you need
-------------
- Your normal Bloodborne folder (the one with Bloodborne.exe) and a save to continue from.
- About 10 minutes.

How to do it
------------
1. Copy this whole folder (amd-motion-test) into your Bloodborne folder, so that Bloodborne.exe sits one level
   above TEST-MOTION.bat.
2. Double-click TEST-MOTION.bat.
3. Run F: press a key in the window, the game starts. Press Continue and play for 3 minutes (walk around,
   fight, whatever you like). Then close the game. If it crashes instead, that is also a useful result:
   just wait for the window to continue.
4. Run G: press a key again, the game starts again. Continue, then play until it crashes or 2 minutes
   pass, then close it.
5. When the window says "Done", a file called bloodborne-motion-test.zip is on your Desktop. Send it to the
   person who gave you this test.

What the two runs are
---------------------
- Run F turns object motion fully on, the way it will ship once it works on AMD cards.
- Run G keeps only the part that reads and writes extra memory from the vertex shaders.
Both write extra counters to the game log. The zip holds the two logs (F.log and G.log) and the name of your
graphics card (gpu.txt). Nothing else is collected.

Notes
-----
- The test only sets its switches for these two runs. When it ends, the game goes back to normal.
- It deletes last_run.log in your saves folder before each run (the game writes a fresh one every time).
- It runs without the saved shader cache, so each run starts clean. Expect more stutter in the first minute.
- If the test cannot find the log, your saves may be in a custom folder. Tell the person who sent you this.
