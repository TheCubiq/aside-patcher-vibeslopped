"""Route real Windows input using freshly inspected native Views geometry."""

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import json
import subprocess

from aside_patcher.windows import browser_processes, is_admin
from scripts.native_ui import NativeUI, ProtocolError


class MouseInput(ctypes.Structure):
    """The documented Windows MOUSEINPUT ABI."""

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class InputPayload(ctypes.Union):
    """Reserve the complete x64 INPUT union, including keyboard payload size."""

    _fields_ = [("mouse", MouseInput), ("storage", ctypes.c_byte * 32)]


class Input(ctypes.Structure):
    """The documented Windows INPUT record."""

    _fields_ = [("type", wintypes.DWORD), ("payload", InputPayload)]


def physical_point(node: dict, window: dict, rectangle: tuple, scale: float, fraction: tuple = (0.5, 0.5)) -> tuple:
    """Map window-relative DIP geometry into physical pixels, including mixed DPI."""
    left, top, right, bottom = rectangle
    if scale <= 0 or any(not 0 < value < 1 for value in fraction):
        raise ValueError("Invalid native input geometry")
    if abs(window["width"] * scale - (right - left)) > 2 or abs(window["height"] * scale - (bottom - top)) > 2:
        raise ValueError("Native and Windows window bounds disagree; input refused")
    x = left + round((node["x"] - window["x"] + node["width"] * fraction[0]) * scale)
    y = top + round((node["y"] - window["y"] + node["height"] * fraction[1]) * scale)
    if not left <= x < right or not top <= y < bottom:
        raise ValueError("Native target lies outside its owning window")
    return x, y


def node_path(root: dict, node_id: int) -> list:
    """Find the inspected node's actual native window ancestry."""
    if root.get("nodeId") == node_id:
        return [root]
    for child in root.get("children", []):
        path = node_path(child, node_id)
        if path:
            return [root, *path]
    return []


def absolute_point(point: tuple, desktop: tuple) -> tuple:
    """Normalize physical virtual-desktop pixels for documented SendInput coordinates."""
    x, y = point
    left, top, width, height = desktop
    if width < 2 or height < 2 or not left <= x < left + width or not top <= y < top + height:
        raise ValueError("Input point is outside the virtual desktop")
    return round((x - left) * 65535 / (width - 1)), round((y - top) * 65535 / (height - 1))


class RoutedInput:
    """Allow input only inside visible windows belonging to the exact test image."""

    def __init__(self, ui: NativeUI, executable):
        if os.name != "nt" or is_admin():
            raise ValueError("Routed tests require an unelevated interactive Windows desktop")
        self.ui, self.executable = ui, executable
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.dwm = ctypes.WinDLL("dwmapi")
        self.dwm.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
        self.dwm.DwmGetWindowAttribute.restype = wintypes.LONG
        self.callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        declarations = {
            "EnumWindows": ([self.callback_type, wintypes.LPARAM], wintypes.BOOL),
            "IsWindowVisible": ([wintypes.HWND], wintypes.BOOL),
            "GetWindowThreadProcessId": ([wintypes.HWND, ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            "GetWindowRect": ([wintypes.HWND, ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            "GetForegroundWindow": ([], wintypes.HWND),
            "GetAncestor": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "GetWindow": ([wintypes.HWND, wintypes.UINT], wintypes.HWND),
            "SetForegroundWindow": ([wintypes.HWND], wintypes.BOOL),
            "WindowFromPoint": ([wintypes.POINT], wintypes.HWND),
            "SetCursorPos": ([ctypes.c_int, ctypes.c_int], wintypes.BOOL),
            "GetCursorPos": ([ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            "SetThreadDpiAwarenessContext": ([ctypes.c_void_p], ctypes.c_void_p),
            "SendInput": ([wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int], wintypes.UINT),
            "GetSystemMetrics": ([ctypes.c_int], ctypes.c_int),
            "GetAsyncKeyState": ([ctypes.c_int], ctypes.c_short),
        }
        for name, (arguments, result) in declarations.items():
            function = getattr(self.api, name)
            function.argtypes, function.restype = arguments, result
        if ctypes.sizeof(Input) != 40:
            raise ValueError("Routed tests require the Windows x64 INPUT ABI")

    def process(self, handle) -> int:
        """Read window ownership instead of matching titles or screen positions."""
        owner = wintypes.DWORD()
        self.api.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        return owner.value

    def windows(self, processes: set) -> list:
        """Enumerate only visible top-level windows of the selected executable."""
        found = []

        @self.callback_type
        def visit(handle, _parameter):
            if self.api.IsWindowVisible(handle) and self.process(handle) in processes:
                rect = wintypes.RECT()
                if self.api.GetWindowRect(handle, ctypes.byref(rect)):
                    found.append((handle, (rect.left, rect.top, rect.right, rect.bottom)))
                # Chromium reports visible frame bounds; GetWindowRect can include
                # invisible resize borders. Use DWM's actual rectangle, never offsets.
                if self.dwm.DwmGetWindowAttribute(handle, 9, ctypes.byref(rect), ctypes.sizeof(rect)) == 0:
                    item = (handle, (rect.left, rect.top, rect.right, rect.bottom))
                    if item not in found:
                        found.append(item)
            return True

        if not self.api.EnumWindows(visit, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return found

    def locate(self, selector: dict, fraction: tuple = (0.5, 0.5), edge: str | None = None) -> tuple:
        """Require native/Win32 bounds agreement before deriving any screen point."""
        node = self.ui.find(**selector)
        bounds = self.ui.connection.call("DOM.getNodeBoundsInScreen", nodeId=node["nodeId"])["boundsInScreen"]
        if edge:
            if edge not in ("before", "after"):
                raise ValueError("Drop position must be before, inside or after a native row")
            # The accepted sidebar spacing is four DIP. Target its midpoint rather
            # than the folder's hit area, which correctly means 'drop into folder'.
            bounds = dict(bounds, y=bounds["y"] - 4 if edge == "before" else bounds["y"] + bounds["height"], height=4)
        ancestry = node_path(self.ui.tree(), node["nodeId"])
        processes = set(browser_processes(self.executable))
        candidates = set()
        windows = self.windows(processes)
        for ancestor in ancestry:
            if ancestor.get("nodeName") != "Window":
                continue
            window = self.ui.connection.call("DOM.getNodeBoundsInScreen", nodeId=ancestor["nodeId"])["boundsInScreen"]
            scale = self.ui.connection.call("DOM.getDeviceScaleFactor", nodeId=ancestor["nodeId"])["deviceScaleFactor"]
            for handle, rectangle in windows:
                try:
                    candidates.add((handle, *physical_point(bounds, window, rectangle, scale, fraction)))
                except ValueError:
                    continue
        frames = {candidate for candidate in candidates if not self.api.GetWindow(candidate[0], 4)}  # GW_OWNER
        if frames:
            candidates = frames
        if len(candidates) != 1:
            raise ProtocolError(f"Cannot uniquely map native node to its test window: {len(candidates)} candidates; windows={windows}")
        return next(iter(candidates))

    def menu_items(self) -> list:
        """Read native Win32 menu labels which Chromium UI DevTools cannot expose."""
        script = Path(__file__).with_name("read_native_menus.ps1")
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Executable",
                str(self.executable),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW,
            check=False,
        )
        if result.returncode:
            raise ProtocolError(f"Native menu accessibility query failed: {result.stderr[:1000]}")
        labels = json.loads(result.stdout)
        if not isinstance(labels, list) or any(not isinstance(label, str) for label in labels):
            raise ProtocolError("Invalid native menu accessibility result")
        return labels

    def guard(self, handle, processes: set) -> None:
        """Never inject into another program or a locked/changed desktop."""
        if self.process(handle) not in processes:
            raise ProtocolError("Input target no longer belongs to the test browser")
        foreground = self.api.GetForegroundWindow()
        if self.process(foreground) not in processes:
            root = self.api.GetAncestor(handle, 3)  # GA_ROOTOWNER
            self.api.SetForegroundWindow(root)
        if self.process(self.api.GetForegroundWindow()) not in processes:
            raise ProtocolError("Test browser could not own foreground input; interactive unlocked desktop required")
        if any(self.api.GetAsyncKeyState(key) & 0x8000 for key in (16, 17, 18, 91, 92)):
            raise ProtocolError("A modifier key is held; routed test input refused")

    def send(self, packets: list) -> None:
        """Queue documented input records and require Windows to accept every one."""
        events = (Input * len(packets))()
        for event, packet in zip(events, packets):
            event.type = 0  # INPUT_MOUSE
            event.payload.mouse = MouseInput(*packet)
        accepted = self.api.SendInput(len(events), events, ctypes.sizeof(Input))
        if accepted != len(events):
            release = (Input * 1)()
            release[0].payload.mouse.dwFlags = 4 | 16  # Never leave a partially queued gesture pressed.
            self.api.SendInput(1, release, ctypes.sizeof(Input))
            raise ProtocolError(f"Windows accepted {accepted}/{len(events)} input records")

    def drag(self, source: dict, target: dict, target_fraction: tuple = (0.5, 0.5), edge: str | None = None) -> None:
        """Move a real bookmark payload through Windows/OLE, then require model assertions."""
        previous = self.api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            source_handle, start_x, start_y = self.ui.wait(lambda: self.locate(source))
            target_handle, end_x, end_y = self.ui.wait(lambda: self.locate(target, target_fraction, edge))
            if self.api.GetAncestor(source_handle, 3) != self.api.GetAncestor(target_handle, 3):
                raise ProtocolError("Cross-window drag requires a separate reviewed scenario")
            processes = set(browser_processes(self.executable))
            self.guard(source_handle, processes)
            desktop = tuple(self.api.GetSystemMetrics(index) for index in (76, 77, 78, 79))
            points = [
                (round(start_x + (end_x - start_x) * step / 16), round(start_y + (end_y - start_y) * step / 16)) for step in range(17)
            ]
            for x, y in points:
                if self.process(self.api.WindowFromPoint(wintypes.POINT(x, y))) not in processes:
                    raise ProtocolError("Drag path crosses another program; input refused")
            if not self.api.SetCursorPos(start_x, start_y):
                raise ctypes.WinError(ctypes.get_last_error())
            packets = [(0, 0, 0, 2, 0, 0)]  # MOUSEEVENTF_LEFTDOWN
            for point in points[1:]:
                x, y = absolute_point(point, desktop)
                packets.append((x, y, 0, 0x0001 | 0x2000 | 0x4000 | 0x8000, 0, 0))
            packets.append((0, 0, 0, 4, 0, 0))  # MOUSEEVENTF_LEFTUP
            self.send(packets)
        finally:
            self.api.SetThreadDpiAwarenessContext(previous)

    def perform(self, selector: dict, button: str | None = None) -> None:
        """Hover or click a live native control through the real Windows input route."""
        previous = self.api.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        if not previous:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            handle, x, y = self.ui.wait(lambda: self.locate(selector))
            processes = set(browser_processes(self.executable))
            self.guard(handle, processes)
            if not self.api.SetCursorPos(x, y):
                raise ctypes.WinError(ctypes.get_last_error())
            if self.process(self.api.WindowFromPoint(wintypes.POINT(x, y))) not in processes:
                raise ProtocolError("Native target is covered by a different program; input refused")
            if button:
                flags = {"left": (2, 4), "right": (8, 16)}[button]
                self.send([(0, 0, 0, flag, 0, 0) for flag in flags])
        finally:
            self.api.SetThreadDpiAwarenessContext(previous)
