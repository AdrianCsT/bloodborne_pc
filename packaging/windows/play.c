/* Bloodborne.exe: starts the game with the settings saved by the launcher, without opening
 * the launcher (it runs `BLauncher.exe --play` from its own folder and waits for it, so Steam
 * and other front ends see the game running). Extra arguments are passed on; `--install-update`
 * goes through without `--play`, because launchers up to v1.6 start the downloaded
 * Bloodborne.exe to install an update. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

int WINAPI wWinMain(HINSTANCE instance, HINSTANCE previous, PWSTR arguments, int show)
{
    (void)instance, (void)previous, (void)show;
    static wchar_t dir[32768], exe[32768], line[32768];
    DWORD length = GetModuleFileNameW(NULL, dir, 32768);
    if (length == 0 || length >= 32768)
        return 1;
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash)
        *slash = 0;
    swprintf(exe, 32768, L"%ls\\BLauncher.exe", dir);
    int update = arguments && wcsncmp(arguments, L"--install-update", 16) == 0;
    swprintf(line, 32768, L"\"%ls\"%ls%ls%ls", exe, update ? L"" : L" --play",
             arguments && *arguments ? L" " : L"", arguments ? arguments : L"");

    STARTUPINFOW startup = {.cb = sizeof(startup)};
    PROCESS_INFORMATION process;
    if (!CreateProcessW(exe, line, NULL, NULL, FALSE, 0, NULL, dir, &startup, &process)) {
        MessageBoxW(NULL, L"BLauncher.exe was not found next to this file.", L"Bloodborne",
                    MB_OK | MB_ICONERROR);
        return 1;
    }
    CloseHandle(process.hThread);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD code = 0;
    GetExitCodeProcess(process.hProcess, &code);
    CloseHandle(process.hProcess);
    return (int)code;
}
