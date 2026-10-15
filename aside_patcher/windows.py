"""Narrow native Windows process, elevation, and installation-lock operations."""

# ctypes field names follow the Windows ABI, not Python attribute conventions.
# pylint: disable=invalid-name,attribute-defined-outside-init

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import struct
import time

from .common.exceptions import Refusal


def file_version(path: Path) -> str:
    """Read the numeric file version from a Windows executable resource.

    Parameters
    ----------
    path : Path
        Executable or DLL whose version resource should be read.

    Returns
    -------
    str
        Four-part numeric file version.
    """
    if os.name != "nt":
        raise Refusal("Browser version resource checks require Windows.")

    version_api = ctypes.WinDLL("version", use_last_error=True)
    version_api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(wintypes.DWORD)]
    version_api.GetFileVersionInfoSizeW.restype = wintypes.DWORD
    version_api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version_api.VerQueryValueW.argtypes = [
        ctypes.c_void_p,
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.UINT),
    ]

    resource_size = version_api.GetFileVersionInfoSizeW(str(path), None)
    if not resource_size:
        raise Refusal(f"Browser file has no readable version resource: {path}")
    resource = ctypes.create_string_buffer(resource_size)
    if not version_api.GetFileVersionInfoW(str(path), 0, resource_size, resource):
        raise Refusal(f"Could not read browser version resource: {path}")

    fixed_info = ctypes.c_void_p()
    fixed_size = wintypes.UINT()
    if not version_api.VerQueryValueW(resource, "\\", ctypes.byref(fixed_info), ctypes.byref(fixed_size)) or fixed_size.value < 52:
        raise Refusal(f"Browser version resource is incomplete: {path}")
    signature, _, major_minor, build_revision = struct.unpack_from("<4I", ctypes.string_at(fixed_info, fixed_size.value))
    if signature != 0xFEEF04BD:
        raise Refusal(f"Browser version resource is invalid: {path}")
    parts = (major_minor >> 16, major_minor & 0xFFFF, build_revision >> 16, build_revision & 0xFFFF)
    return ".".join(str(part) for part in parts)


def browser_processes(executable: Path) -> list[int]:
    """Find running processes by exact image path, including background helpers.

    Parameters
    ----------
    executable : Path
        Selected Aside launcher path.

    Returns
    -------
    list of int
        Process IDs belonging only to this installation.
    """
    if os.name != "nt":
        return []

    class ProcessEntry(ctypes.Structure):
        """Windows PROCESSENTRY32W snapshot record."""

        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.windll.kernel32
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot in (None, ctypes.c_void_p(-1).value):
        raise Refusal("Could not inspect browser processes.")
    entries = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        success = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        if not success:
            raise Refusal("Could not enumerate browser processes.")
        while success:
            if entry.szExeFile.casefold() == "aside.exe":
                process = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                if not process:
                    if kernel.GetLastError() != 87:  # PID vanished after the process snapshot.
                        raise Refusal("A browser process cannot be inspected; close it or retry with the correct user.")
                elif _matches_image(kernel, process, executable):
                    entries.append(entry.th32ProcessID)
            success = kernel.Process32NextW(snapshot, ctypes.byref(entry))
        if kernel.GetLastError() != 18:  # ERROR_NO_MORE_FILES is the only normal snapshot completion.
            raise Refusal("Browser process enumeration ended unexpectedly.")
    finally:
        kernel.CloseHandle(snapshot)
    return entries


def _matches_image(kernel, process, executable):
    """Resolve a snapshot process while tolerating its concurrent normal exit.

    Parameters
    ----------
    kernel : object
        Windows kernel API with declared handle argument types.
    process : int
        Open query handle, always closed here.
    executable : Path
        Exact selected launcher path.

    Returns
    -------
    bool
        Whether the live process belongs to this installation.
    """
    try:
        name, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if kernel.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(length)):
            return Path(name.value).resolve() == executable.resolve()
        code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(process, ctypes.byref(code)) and code.value != 259:
            return False
        raise Refusal("Could not resolve a running browser image.")
    finally:
        kernel.CloseHandle(process)


def require_browser_stopped(executable: Path) -> None:
    """Refuse changes while the selected installation is still running.

    Parameters
    ----------
    executable : Path
        Selected Aside launcher path.
    """
    if browser_processes(executable):
        raise Refusal("The selected Aside installation is still running.")


def launch_arguments(executable: Path) -> list[str]:
    """Capture the existing browser's arguments so restart preserves its profile.

    Parameters
    ----------
    executable : Path
        Exact browser launcher path.

    Returns
    -------
    list of str
        Existing main-process arguments, or an empty list when stopped.
    """
    if not browser_processes(executable):
        return []
    import json  # pylint: disable=import-outside-toplevel

    script = r"""
    $ErrorActionPreference='Stop'
    Import-Module (Join-Path $PSHOME 'Modules\CimCmdlets\CimCmdlets.psd1')
    @(Get-CimInstance Win32_Process -Filter "Name='Aside.exe'" |
      Where-Object ExecutablePath -eq $env:ASIDE_SELECTED_EXE |
      Select-Object -ExpandProperty CommandLine)|ConvertTo-Json -Compress
    """
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=dict(os.environ, ASIDE_SELECTED_EXE=str(executable)),
        check=True,
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    commands = json.loads(result.stdout)
    commands = [commands] if isinstance(commands, str) else commands
    return _main_launch_arguments(commands)


def _main_launch_arguments(commands):
    """Require one main invocation instead of choosing an arbitrary profile session.

    Parameters
    ----------
    commands : list of str
        Exact command lines for the selected executable image.

    Returns
    -------
    list of str
        Unambiguous original main-process arguments.
    """
    if not isinstance(commands, list) or not all(isinstance(command, str) for command in commands):
        raise Refusal("Browser launch context changed; retry before closing any windows.")
    mains = []
    for command in commands:
        arguments = _launch_command_arguments(command)
        if not any(argument == "--type" or argument.startswith("--type=") for argument in arguments):
            mains.append(arguments)
    if len(mains) > 1:
        raise Refusal("Multiple Aside profile sessions are running. Close those sessions before patching; no windows were closed.")
    if not mains:
        raise Refusal("Main browser process could not be identified for a safe restart.")
    return mains[0]


def _launch_command_arguments(command):
    """Decode Windows quoting and release the shell's allocated argument vector.

    Parameters
    ----------
    command : str
        Native command line including its executable name.

    Returns
    -------
    list of str
        Exact arguments excluding the executable name.
    """
    shell = ctypes.windll.shell32
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        raise Refusal("Could not preserve browser launch arguments.")
    try:
        return [pointer[index] for index in range(1, count.value)]
    finally:
        ctypes.windll.kernel32.LocalFree(pointer)


def profile_data_root(arguments: list[str], default: Path) -> Path:
    """Resolve an explicit Chromium user-data directory without guessing its cwd.

    Parameters
    ----------
    arguments : list of str
        Captured main-process arguments.
    default : Path
        Original user's normal profile root.

    Returns
    -------
    Path
        Absolute user-data root for the selected running browser.
    """
    roots = []
    for index, argument in enumerate(arguments):
        if argument.startswith("--user-data-dir="):
            roots.append(argument.partition("=")[2])
        elif argument == "--user-data-dir":
            roots.append(arguments[index + 1] if index + 1 < len(arguments) else "")
    if not roots:
        return default.resolve()
    if len(roots) != 1 or not roots[0] or not Path(roots[0]).is_absolute():
        raise Refusal("Browser uses an ambiguous or relative user-data directory. Select an explicit profile root before patching.")
    return Path(roots[0]).resolve()


def is_admin() -> bool:
    """Return whether the current Windows token is elevated.

    Returns
    -------
    bool
        Elevated token status; False on other platforms.
    """
    return os.name == "nt" and bool(ctypes.windll.shell32.IsUserAnAdmin())


def close_browser(executable: Path, timeout: float = 30) -> bool:
    """Ask only windows belonging to the selected browser to close gracefully.

    Parameters
    ----------
    executable : Path
        Exact selected Aside launcher path.
    timeout : float
        Maximum seconds to wait for process exit.

    Returns
    -------
    bool
        Whether Aside had been running.
    """
    if not browser_processes(executable):
        return False
    if os.name != "nt":
        raise Refusal("Graceful browser shutdown requires Windows.")
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def close_window(window: int, _: int) -> bool:
        """Request closure only for visible windows owned by the exact browser.

        Parameters
        ----------
        window : int
            Window handle supplied by EnumWindows.
        _ : int
            Unused enumeration context.

        Returns
        -------
        bool
            True to continue enumerating windows.
        """
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(pid))
        process = kernel32.OpenProcess(0x1000, False, pid.value)
        if process:
            try:
                name, length = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
                if kernel32.QueryFullProcessImageNameW(process, 0, name, ctypes.byref(length)):
                    if Path(name.value).resolve() == executable.resolve() and user32.IsWindowVisible(window):
                        user32.PostMessageW(window, 0x0010, 0, 0)  # WM_CLOSE, no forced process termination.
            finally:
                kernel32.CloseHandle(process)
        return True

    callback = callback_type(close_window)
    user32.EnumWindows(callback, 0)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and browser_processes(executable):
        time.sleep(0.2)
    if browser_processes(executable):
        raise Refusal(
            "Browser shutdown was blocked. Close Aside completely, then retry; no files were changed.", code="shutdown_blocked"
        )
    return True


def elevate(arguments: list[str], directory: Path) -> int:
    """Run this Python engine through the Windows UAC shell verb and wait.

    Parameters
    ----------
    arguments : list of str
        Arguments for the current Python interpreter.
    directory : Path
        Trusted engine checkout working directory.

    Returns
    -------
    int
        Elevated process exit code.
    """
    import sys  # pylint: disable=import-outside-toplevel

    if os.name != "nt":
        raise Refusal("Windows UAC is unavailable on this platform.")

    class ShellExecuteInfo(ctypes.Structure):
        """Windows SHELLEXECUTEINFOW layout."""

        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIcon", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x40 | 0x100  # SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC
    info.lpVerb, info.lpFile = "runas", sys.executable
    info.lpParameters = subprocess.list2cmdline(arguments)
    info.lpDirectory, info.nShow = str(directory), 0
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    shell.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell.ShellExecuteExW.restype = wintypes.BOOL
    if not shell.ShellExecuteExW(ctypes.byref(info)):
        error = ctypes.get_last_error()
        if error in (5, 1223):  # ERROR_ACCESS_DENIED / ERROR_CANCELLED.
            raise Refusal("Windows elevation was canceled or denied; no files were changed.", code="elevation_declined")
        raise Refusal(
            f"Windows could not launch the installation helper (error {error}); no files were changed.", code="elevation_failed"
        )
    kernel32 = ctypes.windll.kernel32
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    try:
        kernel32.WaitForSingleObject(info.hProcess, 0xFFFFFFFF)
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            raise Refusal("Could not read the elevated helper result.")
        return code.value
    finally:
        kernel32.CloseHandle(info.hProcess)


@contextmanager
def installation_lock(directory: Path):
    """Hold an OS-released file lock for one browser installation.

    Parameters
    ----------
    directory : Path
        Selected browser application directory.

    Yields
    ------
    None
        Exclusive access until the operation exits.
    """
    import msvcrt  # pylint: disable=import-outside-toplevel

    path = directory / ".aside-patcher.lock"
    with path.open("a+b") as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise Refusal("Another patcher operation is using this installation.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
