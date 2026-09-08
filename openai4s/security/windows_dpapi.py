"""Windows user-scoped DPAPI storage accessed only by the WSL Host.

Only JSON travels over stdin/stdout; secrets never enter argv, environment or
an unencrypted file. Stored blobs are bound to the Windows login, the WSL
installation/user and the broker reference. Missing interop fails closed.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from .wsl import powershell_path

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Security
    $request = [Console]::In.ReadToEnd() | ConvertFrom-Json
    if ($request.key -notmatch '^[a-f0-9]{64}$') { throw 'Invalid key' }
    $root = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'OpenAI4S\secrets'
    $path = Join-Path $root ($request.key + '.dpapi')
    $entropy = [Text.Encoding]::UTF8.GetBytes('OpenAI4S/' + $request.key)
    $scope = [Security.Cryptography.DataProtectionScope]::CurrentUser
    $value = $null
    switch ($request.action) {
        'put' {
            $bytes = [Text.Encoding]::UTF8.GetBytes([string]$request.value)
            $encrypted = [Security.Cryptography.ProtectedData]::Protect($bytes, $entropy, $scope)
            [IO.Directory]::CreateDirectory($root) | Out-Null
            $temp = Join-Path $root ([Guid]::NewGuid().ToString() + '.tmp')
            try {
                [IO.File]::WriteAllBytes($temp, $encrypted)
                if ([IO.File]::Exists($path)) {
                    [IO.File]::Replace($temp, $path, $null)
                } else {
                    [IO.File]::Move($temp, $path)
                }
            } finally {
                if ([IO.File]::Exists($temp)) { [IO.File]::Delete($temp) }
            }
        }
        'get' {
            if ([IO.File]::Exists($path)) {
                $bytes = [Security.Cryptography.ProtectedData]::Unprotect([IO.File]::ReadAllBytes($path), $entropy, $scope)
                $value = [Text.Encoding]::UTF8.GetString($bytes)
            }
        }
        'delete' { [IO.File]::Delete($path) }
        default { throw 'Invalid operation' }
    }
    @{ value = $value } | ConvertTo-Json -Compress
} catch {
    [Console]::Error.WriteLine('Windows secure storage operation failed.')
    exit 1
}
"""


def request(
    action: str, namespace: str, scope: str, name: str, value: str | None = None
) -> str | None:
    executable = powershell_path()
    if not executable:
        raise RuntimeError("Windows secure storage requires WSL interoperability")
    identity = Path("/etc/machine-id").read_text("ascii").strip()
    if not identity:
        raise RuntimeError(
            "Windows secure storage requires a persistent WSL machine identity"
        )
    key = hashlib.sha256(
        json.dumps([identity, os.getuid(), namespace, scope, name]).encode("utf-8")
    ).hexdigest()
    try:
        result = subprocess.run(
            [
                executable,
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _SCRIPT,
            ],
            input=json.dumps({"action": action, "key": key, "value": value}).encode(
                "utf-8"
            ),
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Windows secure storage could not be reached") from None
    if result.returncode:
        raise RuntimeError("Windows secure storage operation failed")
    try:
        decoded = json.loads(result.stdout.decode("utf-8-sig"))["value"]
    except (ValueError, KeyError, UnicodeError):
        raise RuntimeError(
            "Windows secure storage returned an invalid response"
        ) from None
    if decoded is not None and not isinstance(decoded, str):
        raise RuntimeError("Windows secure storage returned an invalid value")
    return decoded
