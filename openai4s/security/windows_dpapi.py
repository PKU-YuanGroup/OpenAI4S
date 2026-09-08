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

# Raw stdin/stdout streams rather than [Console]::InputEncoding/OutputEncoding:
# those setters call SetConsoleCP/SetConsoleOutputCP, which throw
# "The handle is invalid" when the process has no console -- the normal case for
# a daemon-spawned interop child -- and they sat outside the try, so the catch
# that prints a sanitized message never ran. Reading and writing UTF-8 bytes
# directly is correct regardless of the host's code page.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
try {
    Add-Type -AssemblyName System.Security
    $buffer = New-Object System.IO.MemoryStream
    [Console]::OpenStandardInput().CopyTo($buffer)
    $request = [Text.Encoding]::UTF8.GetString($buffer.ToArray()) | ConvertFrom-Json
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
        # File.Delete is a no-op on a missing file but throws
        # DirectoryNotFoundException on a missing parent, and $root only exists
        # once something has been stored. The keychain and secret-service
        # backends both ignore a delete that matched nothing; this one raised.
        'delete' { if ([IO.Directory]::Exists($root)) { [IO.File]::Delete($path) } }
        default { throw 'Invalid operation' }
    }
    $payload = [Text.Encoding]::UTF8.GetBytes((@{ value = $value } | ConvertTo-Json -Compress))
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($payload, 0, $payload.Length)
    $stdout.Flush()
} catch {
    [Console]::Error.WriteLine('Windows secure storage operation failed.')
    exit 1
}
"""


#: Read in order. D-Bus keeps the second in step with the first and survives on
#: images that ship no /etc/machine-id at all.
_IDENTITY_FILES = ("/etc/machine-id", "/var/lib/dbus/machine-id")


def machine_identity() -> str:
    """The per-installation half of the storage key.

    Every OS failure here becomes a ``RuntimeError``: this is reached through a
    ``_Backend``, whose callers catch ``SecretBrokerError``, and a bare
    ``FileNotFoundError`` from a distribution that ships no machine-id escaped
    the broker entirely instead of letting ``auto`` move on to the next store.
    """
    for candidate in _IDENTITY_FILES:
        try:
            identity = Path(candidate).read_text("ascii").strip()
        except (OSError, ValueError):
            continue
        if identity:
            return identity
    raise RuntimeError(
        "Windows secure storage requires a persistent WSL machine identity"
    )


def request(
    action: str, namespace: str, scope: str, name: str, value: str | None = None
) -> str | None:
    try:
        executable = powershell_path()
    except OSError:
        raise RuntimeError("Windows secure storage could not be reached") from None
    if not executable:
        raise RuntimeError("Windows secure storage requires WSL interoperability")
    identity = machine_identity()
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
            # WSLENV is the only thing that carries a Linux variable across
            # into the Windows process. Emptying it keeps the daemon's own
            # environment -- which holds the very credentials this module
            # exists to protect -- on the Linux side of the interop boundary.
            env={**os.environ, "WSLENV": ""},
            # Same reason `tools/dynamic.py` and the keychain backend's writes
            # do: an interop child must not inherit the daemon's controlling
            # terminal, and with it the TIOCSTI escape.
            start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        raise RuntimeError("Windows secure storage could not be reached") from None
    if result.returncode:
        raise RuntimeError("Windows secure storage operation failed")
    try:
        decoded = json.loads(result.stdout.decode("utf-8-sig"))["value"]
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise RuntimeError(
            "Windows secure storage returned an invalid response"
        ) from None
    if decoded is not None and not isinstance(decoded, str):
        raise RuntimeError("Windows secure storage returned an invalid value")
    return decoded
