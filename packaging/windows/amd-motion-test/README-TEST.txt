Bloodborne PC: AMD motion test
==============================

Why this test exists
--------------------
On some AMD graphics cards the game stops with "Device lost" a few seconds after a save loads, when the
"object motion" feature is on. To keep the game stable, that feature is switched off on AMD cards by default.
We think we found safer ways to run it, and we need your card to tell us whether they work.

What you need
-------------
- Your normal Bloodborne folder (the one with Bloodborne.exe) and a save to continue from.
- About 10 minutes.

How to do it
------------
1. Copy this whole folder (amd-motion-test) into your Bloodborne folder, so that Bloodborne.exe sits one level
   above TEST-MOTION.bat.
2. Double-click TEST-MOTION.bat.
3. Run A: press a key in the window, the game starts. Press Continue and play for about 2 minutes (walk
   around, fight, whatever you like). Then close the game.
   If the game closes by itself with an error, that is a useful result. Just wait for the window to continue.
4. Run B: press a key again, the game starts again. Do exactly the same for about 2 minutes, then close it.
5. When the window says "Done", a file called bloodborne-motion-test.zip is on your Desktop. Send it to the
   person who gave you this test.

What the two runs are
---------------------
- Run A turns object motion on with the standard settings.
- Run B turns it on with a different, simpler way of writing the data.
Both write extra counters to the game log. The zip holds the two logs (A.log and B.log) and the name of your
graphics card (gpu.txt). Nothing else is collected.

Notes
-----
- The test only sets its switches for these two runs. When it ends, the game goes back to normal.
- It deletes user\last_run.log before each run (the game writes a fresh one every time).
- If the test cannot find the log, your saves may be in a custom folder. Tell the person who sent you this.
