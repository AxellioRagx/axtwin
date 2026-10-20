# Enable RDP on a domain-joined Windows member
# (`docs/WORK_ORDER_protocol_faithful_services.md` S3, §4 row 2).
#
# 3389/tcp is OBSERVED on four of the source's local hosts and is OFF by
# default on a fresh Windows image, so a twin that reproduces those hosts
# without this answers nothing on the one port the capture most clearly
# says they served. Everything here is a Windows BUILT-IN -- no package, no
# egress, no bake: the service (TermService) is already installed and
# running on every Windows image; all that is missing is the policy bit and
# the firewall rule.
#
# Delivered as an `install: [{script: ...}]` action, i.e. `OP-APP-030
# run-script`, which on a Windows host writes this file into the guest and
# runs `powershell -NoProfile -ExecutionPolicy Bypass -File <path>`
# (`config_exec/backend.py::_run_script_windows`). It is therefore PLAIN
# POWERSHELL with no shebang -- a POSIX heredoc dies here, which is the
# exact failure that killed the superseded echo-responder approach.
#
# FAIL-LOUD (LES-001): every step is read back and a failed readback throws,
# so a green op means RDP is actually listening -- not merely that four
# commands exited 0.
$ErrorActionPreference = 'Stop'

$tsPath = 'HKLM:\System\CurrentControlSet\Control\Terminal Server'

# 1. The policy bit that makes TermService accept connections at all.
Set-ItemProperty -Path $tsPath -Name 'fDenyTSConnections' -Value 0

# 2. Network Level Authentication ON. Not cosmetic for this work order: NLA
#    is what makes the connection carry a CredSSP/Kerberos exchange, so the
#    twin's RDP traffic looks like the source's on the wire rather than like
#    a bare X.224 handshake to nowhere.
Set-ItemProperty -Path "$tsPath\WinStations\RDP-Tcp" -Name 'UserAuthentication' -Value 1

# 3. The built-in firewall group (three rules: TCP, UDP, shadow), addressed
#    by its RESOURCE ID, not its display name: `-DisplayGroup 'Remote
#    Desktop'` is the localized string and matches nothing on a non-English
#    Windows image, which fails silently (zero rules enabled, exit 0) --
#    caught only by step 5's readback. `@FirewallAPI.dll,-28752` is the same
#    group, locale-independent.
Enable-NetFirewallRule -Group '@FirewallAPI.dll,-28752'

# 4. The service itself -- present on every Windows image, but not
#    necessarily started on a freshly-specialized clone.
$svc = Get-Service -Name TermService
if ($svc.Status -ne 'Running') { Start-Service -Name TermService }

# 5. Read every one of them back. `Get-NetTCPConnection` is the one that
#    matters: it is the same fact an in-guest `ss -lnH -tu` (the scorecard's
#    row 4) and a protocol-aware probe (row 5) will look for.
if ((Get-ItemProperty -Path $tsPath).fDenyTSConnections -ne 0) {
    throw 'enable-rdp: fDenyTSConnections did not take (still denying connections)'
}
if ((Get-Service -Name TermService).Status -ne 'Running') {
    throw 'enable-rdp: TermService is not running'
}

# TermService creates the RDP-Tcp listener ASYNCHRONOUSLY after the registry
# flip -- a single immediate read is a race that fails a correct host. Poll to
# a deadline and throw only there: still fail-loud (a host that never listens
# fails the op), but no longer flaky. 60s total is generous next to the
# minutes a domain join already costs (GAP-051).
$deadline = (Get-Date).AddSeconds(60)
$listener = $null
while (-not $listener -and (Get-Date) -lt $deadline) {
    $listener = Get-NetTCPConnection -State Listen -LocalPort 3389 -ErrorAction SilentlyContinue
    if (-not $listener) { Start-Sleep -Seconds 2 }
}
if (-not $listener) {
    throw 'enable-rdp: nothing is listening on 3389/tcp 60s after enabling RDP'
}
Write-Output 'enable-rdp: 3389/tcp listening'
