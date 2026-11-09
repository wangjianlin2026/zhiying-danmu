from __future__ import annotations

import base64
import hashlib
import json
import os
import platform
import subprocess
import threading
import uuid
from datetime import datetime
from pathlib import Path


def _get_disk_serial_fallback() -> str | None:
    """WMI 不可用时，用 PowerShell 读取第一块物理硬盘序列号。"""
    try:
        command = (
            "(Get-CimInstance Win32_DiskDrive | "
            "Where-Object { $_.SerialNumber -and $_.SerialNumber.Trim() } | "
            "Select-Object -First 1 -ExpandProperty SerialNumber)"
        )
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=flags,
        )
        serial = (result.stdout or "").strip()
        return serial or None
    except Exception:
        return None


def machine_code() -> str:
    """按原版算法生成机器码，确保能识别原版已保存的授权文件。"""
    system_info = platform.system() + platform.release() + platform.machine()
    stable_id = None
    try:
        import wmi

        for disk in wmi.WMI().Win32_DiskDrive():
            serial = (disk.SerialNumber or "").strip()
            if serial:
                stable_id = f"DISK_{serial}"
                break
    except Exception:
        pass
    if not stable_id:
        serial = _get_disk_serial_fallback()
        if serial:
            stable_id = f"DISK_{serial}"
    if not stable_id:
        mac = ":".join(
            f"{(uuid.getnode() >> elements) & 255:02x}"
            for elements in range(0, 12, 2)
        )[::-1]
        stable_id = f"MAC_{mac}"
    if not stable_id:
        stable_id = f"SYS_{system_info}"

    processor = platform.processor()
    if processor:
        if "AMD" in processor.upper():
            stable_processor = "AMD64"
        elif "INTEL" in processor.upper():
            stable_processor = "Intel64"
        else:
            stable_processor = "AMD64"
    else:
        stable_processor = "AMD64"

    raw = f"{system_info}|{stable_id}|{stable_processor}"
    digest = hashlib.md5(raw.encode("utf-8")).hexdigest().upper()
    return "-".join(digest[index:index + 4] for index in range(0, 32, 4))


class LicenseManager:
    def __init__(self):
        home_license = Path.home() / ".douyintm_license"
        candidates = []
        override = os.environ.get("DOUYINTM_LICENSE_PATH")
        if override:
            candidates.append(Path(override).expanduser())
        candidates.append(home_license)
        home_drive = os.environ.get("HOMEDRIVE")
        home_path = os.environ.get("HOMEPATH")
        if home_drive and home_path:
            candidates.append(
                Path(home_drive + os.sep) / home_path.lstrip("\\/") / ".douyintm_license"
            )
        username = os.environ.get("USERNAME")
        system_drive = os.environ.get("SystemDrive", "C:")
        if username:
            candidates.append(
                Path(system_drive + os.sep)
                / "Users"
                / username
                / ".douyintm_license"
            )
        candidates.append(
            Path(__file__).resolve().parent.parent / ".douyintm_license"
        )
        self.license_file = home_license
        for candidate in dict.fromkeys(candidates):
            if candidate and candidate.exists():
                self.license_file = candidate
                break
        self.used_codes_file = self.license_file.parent / ".douyintm_used_codes"
        self._lock = threading.Lock()

    @staticmethod
    def _key(machine: str) -> bytes:
        return hashlib.sha256(machine.encode("utf-8")).digest()

    def _encrypt(self, text: str, machine: str) -> str:
        key = self._key(machine)
        data = text.encode("utf-8")
        encrypted = bytes(value ^ key[index % len(key)] for index, value in enumerate(data))
        return base64.b64encode(encrypted).decode("ascii")

    def _decrypt(self, text: str, machine: str) -> str | None:
        try:
            key = self._key(machine)
            data = base64.b64decode(text.encode("ascii"))
            decrypted = bytes(value ^ key[index % len(key)] for index, value in enumerate(data))
            return decrypted.decode("utf-8")
        except Exception:
            return None

    def _decrypt_code(self, code: str, machine: str) -> dict | None:
        try:
            clean = code.replace("-", "").replace(" ", "")
            if len(clean) < 20:
                return None
            data = base64.b64decode(clean.encode("ascii"))
            key = self._key(machine)
            decrypted = bytes(value ^ key[index % len(key)] for index, value in enumerate(data))
            result = json.loads(decrypted.decode("utf-8"))
            return result if isinstance(result, dict) else None
        except Exception:
            return None

    def _is_used(self, code: str) -> bool:
        try:
            if not self.used_codes_file.exists():
                return False
            value = hashlib.sha256(code.encode("utf-8")).hexdigest()
            return value in self.used_codes_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return False

    def _mark_used(self, code: str) -> bool:
        try:
            self.used_codes_file.parent.mkdir(parents=True, exist_ok=True)
            value = hashlib.sha256(code.encode("utf-8")).hexdigest()
            with self.used_codes_file.open("a", encoding="utf-8") as handle:
                handle.write(value + "\n")
            return True
        except OSError:
            return False

    def status(self, machine: str | None = None) -> dict:
        machine = machine or machine_code()
        try:
            if not self.license_file.exists():
                return {"activated": False, "message": "未找到授权文件"}
            encrypted = self.license_file.read_text(encoding="utf-8").strip()
            if not encrypted:
                return {"activated": False, "message": "授权文件为空"}
            decrypted = self._decrypt(encrypted, machine)
            if not decrypted:
                return {"activated": False, "message": "授权数据解密失败，机器码可能不匹配"}
            data = json.loads(decrypted)
            if data.get("machine_code") != machine:
                return {"activated": False, "message": "机器码不匹配"}
            expiry = int(data.get("expiry_timestamp", 0))
            now = int(datetime.now().timestamp())
            if expiry and now > expiry:
                return {"activated": False, "message": "授权已过期"}
            remaining = 999999 if not expiry else max(0, (expiry - now) // 86400)
            return {
                "activated": True,
                "machine_code": machine,
                "license_type": data.get("license_type", "未知"),
                "expiry_date": data.get("expiry_date", ""),
                "remaining_days": remaining,
                "message": "授权有效",
            }
        except Exception as exc:
            return {"activated": False, "message": f"获取授权状态失败: {exc}"}

    def activate(self, code: str, machine: str | None = None) -> dict:
        machine = machine or machine_code()
        code = code.strip()
        if not code:
            return {"valid": False, "message": "授权码为空"}
        with self._lock:
            if self._is_used(code):
                return {"valid": False, "message": "此授权码已被使用"}
            data = self._decrypt_code(code, machine)
            if not data:
                return {"valid": False, "message": "授权码格式错误或解密失败"}
            if data.get("machine_code") != machine:
                return {"valid": False, "message": "激活码与当前机器不匹配"}
            if not self._mark_used(code):
                return {"valid": False, "message": "授权码标记失败"}
            days = int(data.get("expiry_days", 30))
            permanent = bool(data.get("permanent")) or days <= 0
            now = datetime.now()
            if permanent:
                expiry = 0.0
            else:
                expiry = now.timestamp() + days * 86400
            payload = {
                "machine_code": machine,
                "license_type": data.get(
                    "license_type", "永久" if permanent else f"{days}天"
                ),
                "activation_date": now.isoformat(),
                "expiry_date": (
                    "" if permanent else datetime.fromtimestamp(expiry).isoformat()
                ),
                "expiry_timestamp": expiry,
                "expiry_days": days,
                "total_expiry_days": days,
                "permanent": permanent,
                "version": "2.0",
                "last_updated": now.isoformat(),
            }
            encrypted = self._encrypt(
                json.dumps(payload, ensure_ascii=False, separators=(",", ":")), machine
            )
            try:
                self.license_file.parent.mkdir(parents=True, exist_ok=True)
                temp = self.license_file.with_suffix(".tmp")
                temp.write_text(encrypted, encoding="utf-8")
                os.replace(temp, self.license_file)
            except OSError as exc:
                return {"valid": False, "message": f"保存授权失败: {exc}"}
            return {
                "valid": True,
                "message": f"激活码验证成功，{payload['license_type']}",
                "remaining_days": 999999 if permanent else days,
            }

    def clear(self) -> bool:
        try:
            self.license_file.unlink(missing_ok=True)
            self.used_codes_file.unlink(missing_ok=True)
            return True
        except OSError:
            return False
