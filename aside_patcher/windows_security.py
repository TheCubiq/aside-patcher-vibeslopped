"""Preserve the initiating user's access to an elevated helper's result file."""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import os
from pathlib import Path
from typing import Iterator, TextIO

from .common.exceptions import Refusal


class Trustee(ctypes.Structure):
    """Windows TRUSTEE_W with a SID rather than a trustee name."""

    _fields_ = [
        ("multiple_trustee", ctypes.c_void_p),
        ("multiple_operation", wintypes.DWORD),
        ("form", wintypes.DWORD),
        ("kind", wintypes.DWORD),
        ("sid", ctypes.c_void_p),
    ]


class ExplicitAccess(ctypes.Structure):
    """Windows EXPLICIT_ACCESS_W describing one file access grant."""

    _fields_ = [
        ("permissions", wintypes.DWORD),
        ("mode", wintypes.DWORD),
        ("inheritance", wintypes.DWORD),
        ("trustee", Trustee),
    ]


@contextmanager
def helper_result(request_path: Path, result_path: Path) -> Iterator[TextIO]:
    """Create the helper output exclusively with rights to set its own ACL.

    Parameters
    ----------
    request_path : Path
        Ticket owned by the initiating user.
    result_path : Path
        New sibling result file; existing paths are never followed or overwritten.

    Yields
    ------
    TextIO
        Writable result stream already accessible to the initiating user.
    """
    import msvcrt  # pylint: disable=import-outside-toplevel

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    # GENERIC_WRITE | READ_CONTROL | WRITE_DAC, no sharing, CREATE_NEW.
    handle = kernel.CreateFileW(str(result_path), 0x40060000, 0, None, 1, 0x80, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY | os.O_BINARY)
    except (OSError, ValueError):
        kernel.CloseHandle(handle)
        raise

    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        grant_result_access(request_path, stream)
        yield stream


def grant_result_access(request_path: Path, result_stream: TextIO) -> None:
    """Let the ticket's owner read and clean up the helper's new result file.

    Parameters
    ----------
    request_path : Path
        Request file created by the original unelevated process.
    result_stream : TextIO
        Result file still open after exclusive creation by the elevated worker.
        Permissions are applied to this handle, never to a replaceable path.
    """
    import msvcrt  # pylint: disable=import-outside-toplevel

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    pointer_output = ctypes.POINTER(ctypes.c_void_p)
    query_arguments = [wintypes.DWORD, wintypes.DWORD] + [pointer_output] * 5
    security.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR] + query_arguments
    security.GetSecurityInfo.argtypes = [wintypes.HANDLE] + query_arguments
    security.SetEntriesInAclW.argtypes = [wintypes.ULONG, ctypes.POINTER(ExplicitAccess), ctypes.c_void_p, pointer_output]
    security.SetSecurityInfo.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD] + [ctypes.c_void_p] * 4
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    owner = ctypes.c_void_p()
    old_acl = ctypes.c_void_p()
    new_acl = ctypes.c_void_p()
    request_descriptor = ctypes.c_void_p()
    result_descriptor = ctypes.c_void_p()
    handle = msvcrt.get_osfhandle(result_stream.fileno())

    try:
        # SE_FILE_OBJECT=1; OWNER_SECURITY_INFORMATION=1.
        error = security.GetNamedSecurityInfoW(
            str(request_path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(request_descriptor)
        )
        if error or not owner.value:
            raise Refusal(f"Could not identify the helper ticket's owner (Windows error {error}).")

        # DACL_SECURITY_INFORMATION=4. Retain existing system/administrator access.
        error = security.GetSecurityInfo(handle, 1, 4, None, None, ctypes.byref(old_acl), None, ctypes.byref(result_descriptor))
        if error:
            raise Refusal(f"Could not read helper result permissions (Windows error {error}).")

        access = ExplicitAccess(
            permissions=0x1F01FF,  # FILE_ALL_ACCESS, including cleanup of this temporary file.
            mode=1,  # GRANT_ACCESS.
            trustee=Trustee(sid=owner.value),  # Zero-initialized form is TRUSTEE_IS_SID.
        )
        error = security.SetEntriesInAclW(1, ctypes.byref(access), old_acl, ctypes.byref(new_acl))
        if error:
            raise Refusal(f"Could not prepare helper result permissions (Windows error {error}).")
        error = security.SetSecurityInfo(handle, 1, 4, None, None, new_acl, None)
        if error:
            raise Refusal(f"Could not grant the initiating user access to the helper result (Windows error {error}).")
    finally:
        for allocation in (new_acl, result_descriptor, request_descriptor):
            if allocation.value:
                kernel.LocalFree(allocation)
