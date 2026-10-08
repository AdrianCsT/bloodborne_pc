Bloodborne PC: AMD motion test
==============================

Why this test exists
--------------------
On some AMD graphics cards the game stops with "Device lost" a few seconds after a save loads, when the
"object motion" feature is on. To keep the game stable, that feature is switched off on AMD cards by default.
We are narrowing down which part of the feature your card does not like, and we need your card to tell us.

What you need
-------------
- Your normal Bloodborne folder (the one with Bloodborne.exe) and a save to continue from.
- About 15 minutes.

How to do it
------------
1. Copy this whole folder (amd-motion-test) into your Bloodborne folder, so that Bloodborne.exe sits one level
   above TEST-MOTION.bat.
2. Double-click TEST-MOTION.bat.
3. Run C: press a key in the window, the game starts. Press Continue and play until the game crashes or
   2 minutes pass (walk around, fight, whatever you like). Then close the game.
   The game may crash right after the save loads. That is expected and is a useful result. If it closes by
   itself with an error, just wait for the window to continue.
4. Run D: press a key again, the game starts again. Do exactly the same: Continue, then play until it
   crashes or 2 minutes pass, then close it.
5. Run E: the same once more.
6. When the window says "Done", a file called bloodborne-motion-test.zip is on your Desktop. Send it to the
   person who gave you this test.

What the three runs are
-----------------------
Each run turns object motion on and switches off a different part of it:
- Run C leaves out the part that reads and writes extra memory from the vertex shaders.
- Run D leaves out the extra picture the feature draws into.
- Run E leaves out the values passed between the shader stages.
All three write extra counters to the game log. The zip holds the three logs (C.log, D.log and E.log) and the
name of your graphics card (gpu.txt). Nothing else is collected.

Notes
-----
- The test only sets its switches for these three runs. When it ends, the game goes back to normal.
- It deletes last_run.log in your saves folder before each run (the game writes a fresh one every time).
- It runs without the saved shader cache, so each run starts clean. Expect more stutter in the first minute.
- If the test cannot find the log, your saves may be in a custom folder. Tell the person who sent you this.
