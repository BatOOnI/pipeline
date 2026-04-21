import base64
import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path
from typing import Any, Dict


class SecureApiKeyStore:
    def __init__(self) -> None:
        home = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        self.store_file = home / "AvadaSeoGenerator" / "openai_api_key.dat"

    @staticmethod
    def _is_windows() -> bool:
        return os.name == "nt"

    @staticmethod
    def _crypt_protect(data: bytes) -> bytes:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()

        if not crypt32.CryptProtectData(
            ctypes.byref(in_blob),
            "OpenAI API Key",
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise RuntimeError("Nie udalo sie zaszyfrowac klucza (DPAPI).")

        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    @staticmethod
    def _crypt_unprotect(data: bytes) -> bytes:
        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32

        in_blob = DATA_BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data), ctypes.POINTER(ctypes.c_char)))
        out_blob = DATA_BLOB()

        if not crypt32.CryptUnprotectData(
            ctypes.byref(in_blob),
            None,
            None,
            None,
            None,
            0,
            ctypes.byref(out_blob),
        ):
            raise RuntimeError("Nie udalo sie odszyfrowac klucza (DPAPI).")

        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)

    def save(self, api_key: str) -> None:
        key = api_key.strip()
        if not key:
            raise ValueError("Klucz API jest pusty.")
        self.store_file.parent.mkdir(parents=True, exist_ok=True)
        raw = key.encode("utf-8")
        payload = self._crypt_protect(raw) if self._is_windows() else raw
        self.store_file.write_text(base64.b64encode(payload).decode("ascii"), encoding="utf-8")

    def load(self) -> str:
        if not self.store_file.exists():
            return ""
        payload = self.store_file.read_text(encoding="utf-8").strip()
        if not payload:
            return ""
        decoded = base64.b64decode(payload)
        raw = self._crypt_unprotect(decoded) if self._is_windows() else decoded
        return raw.decode("utf-8")

    def clear(self) -> None:
        if self.store_file.exists():
            self.store_file.unlink()


class SessionDraftStore:
    def __init__(self) -> None:
        home = Path(os.getenv("LOCALAPPDATA", str(Path.home())))
        self.file_path = home / "AvadaSeoGenerator" / "session_draft.json"

    def save(self, data: Dict[str, Any]) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.file_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self) -> Dict[str, Any]:
        if not self.file_path.exists():
            return {}
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
