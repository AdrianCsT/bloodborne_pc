# Contributing to Bloodborne PC

Thanks for helping. This is the Windows fork of the Bloodborne PS4 port. The most useful contribution is often a good bug report.

## Report a problem

Use the [bug report form](https://github.com/0xCydral/bloodborne_pc/issues/new?template=bug_report.yml). Give the port version (shown at the top of the launcher), your graphics card and driver, and what happened. Attach `user\last_run.log`: it starts with your graphics card model, memory and driver, and it says how the game ran.

Setup and performance questions get faster answers on the original project's [Discord server](https://discord.gg/KYZRKk9CB). The Linux version lives in the [upstream project](https://github.com/deadinside28/bloodborne_pc).

## Never share game material

Do not attach or commit game files, a decrypted `eboot.bin`, `.pkg` files, extracted game assets, keys or firmware, in issues, pull requests or anywhere else. The port ships no game art.

## Send a code change

1. Build the project and run the tests first, as described under [Building from source](README.md#building-from-source) in the README:

   ```bash
   bash build.sh
   python3 -m unittest discover -s tests
   ```

   The C and C++ tests build with `bash build.sh --test`. The renderer tests build with `ninja -C out/gpu motion-history-test cache-consistency-test ui-composition-test scene-resolution-test motion-shader-test settings-test`.

2. Keep the pull request to one change. A fix and an unrelated cleanup are two pull requests.
3. Add or update a test. The Python tests are in `tests/test_*.py`, the C and C++ tests in `tests/test_*.c` and `tests/test_*.cpp`. If a change cannot be tested without the game, say how you checked it in the pull request.
4. Write commit messages as [Conventional Commits](https://www.conventionalcommits.org), for example `fix(gpu): skip a failed pipeline cache save` or `docs(readme): explain the data folder`.
5. Open the pull request against `develop`. Betas and daily work live there, and `master` follows it when a release ships (see [RELEASING.md](RELEASING.md)).

## Conventions

- Code under `gpu/shadps4/` is vendored from shadPS4. Mark each local change with a `bbport:` comment and keep the upstream commit in `gpu/VENDOR.txt` accurate.
- New Python and shell files carry the `SPDX-License-Identifier: GPL-2.0-or-later` header. Contributions are licensed under the same terms as the project ([LICENSE](LICENSE)).
- Comments say why, not what. Prefer a short function with a clear name over a long comment.
