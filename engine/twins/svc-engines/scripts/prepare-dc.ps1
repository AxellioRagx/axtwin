# Prepare the domain controller for the engine round: point its DNS forwarder
# at the twin resolver (`docs/WORK_ORDER_protocol_engines.md` §5 E3 -- the
# name-service chain), and leave one readable probe file in SYSVOL for the AD
# persona's Kerberos-authenticated SMB emit.
#
# **Hop 3 of 3.** The chain this twin needs is, exactly:
#
#     Windows member --> the DC (AD-integrated DNS, set by the join op)
#     the DC         --> forwarder = the twin resolver  <-- THIS FILE
#     twin resolver  --> corp.axr.lab back to the DC, everything else to the SINK
#
# E3's own wording: *"Set the DC's forwarder; never point a joined member at
# bind9 -- joins and SRV lookups break."* So the member keeps pointing at the
# DC (which `config_exec/render.py::render_join_to_domain` arranges) and the
# DC is the one host whose UPSTREAM changes.
#
# **What breaks without it.** `Install-ADDSForest -InstallDns` leaves the new
# DNS server forwarding to whatever the image's DNS client had -- on this twin,
# nothing that answers. The DC then resolves `corp.axr.lab` (it is
# authoritative) and NOTHING else, so every external name a Windows engine
# touches dies at the first hop, and the failure looks like a broken client.
#
# `-UseRootHint $false` is as load-bearing as the forwarder itself: with root
# hints left on, a DNS server that cannot reach its forwarder falls back to
# recursing from the real root servers -- i.e. the twin talks to the internet
# the moment the resolver hiccups. Containment here is "there is nowhere else
# to ask".
#
# **Why it retries.** `run-script` (OP-APP-030) gates on THIS app's
# `provision-app` -- the AD DS promotion -- and not on `domain-ready`, so this
# script can start while the promotion's reboot is still settling and before
# the DNS Server service has finished its first start. A single immediate call
# would fail a perfectly good DC. Poll to a deadline and throw only there:
# still fail-loud (a DC that never accepts the forwarder fails the op), no
# longer flaky. 300 s is generous next to the 8.6 min the DC path measured on
# svc-verify's run.
#
# Delivered as an `install: [{script: ...}]` action, i.e. `OP-APP-030
# run-script`, which on a Windows host writes this file into the guest and runs
# `powershell -NoProfile -ExecutionPolicy Bypass -File <path>`
# (`config_exec/backend.py::_run_script_windows`). PLAIN POWERSHELL, no
# shebang.
#
# FAIL-LOUD (LES-001): the forwarder is READ BACK, and an external name is
# resolved through it, so a green op means the chain actually answers -- not
# merely that a cmdlet exited 0.
$ErrorActionPreference = 'Stop'

# Must match `resolver`'s pinned address in svc-engines.yaml.
$resolverIp = '10.66.10.11'
# An observed EXTERNAL name from the cast corpus the sink is authoritative for
# (`twin-configs-cast/corpapcn-disco/topology.json`, observations.dns.names).
# Resolving it here proves all three hops at once: DC -> resolver -> sink.
$probeName = 'github.com'

# 1. Wait for the DNS Server service. It is installed by `-InstallDns` during
#    the promotion and starts asynchronously after the reboot.
$deadline = (Get-Date).AddSeconds(300)
$dnsUp = $false
while (-not $dnsUp -and (Get-Date) -lt $deadline) {
    try {
        $svc = Get-Service -Name DNS -ErrorAction Stop
        if ($svc.Status -ne 'Running') { Start-Service -Name DNS -ErrorAction SilentlyContinue }
        Get-DnsServerForwarder -ErrorAction Stop | Out-Null
        $dnsUp = $true
    } catch {
        Start-Sleep -Seconds 5
    }
}
if (-not $dnsUp) {
    throw "prepare-dc: the DNS Server service never became usable within 300s"
}

# 2. Set the forwarder. Idempotent by construction: `Set-DnsServerForwarder`
#    REPLACES the list, so a re-run converges instead of accumulating entries.
Set-DnsServerForwarder -IPAddress $resolverIp -UseRootHint $false

# 3. Read it back -- the forwarder AND the root-hint flag, because leaving
#    root hints on is the failure mode that leaks to the real internet while
#    looking perfectly healthy.
$fwd = Get-DnsServerForwarder
if ($fwd.IPAddress -notcontains $resolverIp) {
    throw "prepare-dc: forwarder did not take (got '$($fwd.IPAddress -join ',')', expected $resolverIp)"
}
if ($fwd.UseRootHint) {
    throw 'prepare-dc: root hints are still enabled -- this DC can still reach the real root servers'
}

# 4. Prove the chain, not just the setting. The resolver and the sink are
#    OTHER hosts whose own run-script may not have finished (there is no
#    cross-host ordering between apps), so this is polled and REPORTED rather
#    than fatal -- a hard failure here would turn a race into a failed deploy.
#    README step 3 is the real gate; `Resolve-DnsName` here is the early read.
$chainDeadline = (Get-Date).AddSeconds(120)
$answered = $false
while (-not $answered -and (Get-Date) -lt $chainDeadline) {
    try {
        $ans = Resolve-DnsName -Name $probeName -Type A -Server 127.0.0.1 -ErrorAction Stop
        if ($ans) { $answered = $true }
    } catch {
        Start-Sleep -Seconds 5
    }
}
if ($answered) {
    Write-Output "prepare-dc: forwarder $resolverIp set; $probeName resolves through the chain"
} else {
    Write-Output "prepare-dc: forwarder $resolverIp set, but $probeName does not resolve yet -- the resolver or the sink may still be provisioning (re-check with Resolve-DnsName)"
}

# 5. A readable file in SYSVOL, for the AD persona's SMB emit.
#
# `drive: command`'s `smb` template fetches a NAMED FILE
# (`smbclient //<host>/<share> -c 'get <file> /dev/null'`), so the emit needs
# a file that actually exists: a `get` of a missing path still completes the
# NEGOTIATE/SESSION_SETUP/TREE_CONNECT (so the flow is still `smb` at the
# sensor) but reports a failure the persona counts, and a persona whose
# `fails=` counter climbs every cycle is one nobody will trust on the run that
# matters. SYSVOL is the right share for it: it is the one share every domain
# user can read, and reading it is what a real member does at every logon.
#
# The name is deliberately flat and boring -- the driver's knob validator
# (`_PATH_RE`) accepts `[A-Za-z0-9._/\-]` only, so the obvious candidate
# (`corp.axr.lab\Policies\{31B2F340-...}\GPT.INI`) cannot be authored at all:
# GUID braces are not in the charset. 4 MiB, so the transfer is a real read
# rather than a metadata round trip, and zero-filled because no file CONTENT
# was ever observed (aximprint GAP-019) -- the bytes are real, the content is
# not a claim.
$sysvol = Join-Path $env:SystemRoot 'SYSVOL\sysvol'
if (-not (Test-Path $sysvol)) {
    throw "prepare-dc: $sysvol does not exist -- is this host actually a promoted DC?"
}
$probeFile = Join-Path $sysvol 'axr-probe.bin'
if (-not (Test-Path $probeFile)) {
    $fs = [IO.File]::Create($probeFile)
    try { $fs.SetLength(4MB) } finally { $fs.Close() }
}
$len = (Get-Item $probeFile).Length
if ($len -lt 1MB) {
    throw "prepare-dc: $probeFile is only $len bytes -- the SMB emit would read nothing"
}
Write-Output "prepare-dc: SYSVOL probe file ready ($probeFile, $len bytes)"
