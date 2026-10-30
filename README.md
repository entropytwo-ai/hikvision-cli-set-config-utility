# Hikvision CLI Set-Config Utility (`hik_config.py`)

Bulk **read and write** configuration on many Hikvision (and Hikvision-OEM) cameras at once, over the
standard **ISAPI** REST interface, without logging into each web UI and without Hikvision tools.

Version: 2.0.0. This is a full rewrite of the original single-purpose `set-hik-ntp.py` script.

<table><tr>
<td><img src=".github/donate-bitcoin-qr.png" width="96" alt="Bitcoin donation QR code"></td>
<td><sub>💛 If you find this project useful, I'd be grateful for any donation. Thank you!<br>
<b>Bitcoin:</b> <code>bc1qu6dkcchurnutgc40y6n53s3zm588z9nnkm2ygq</code></sub></td>
</tr></table>

## Table of Contents

- [Overview](#overview)
  - [Features](#features)
  - [How It Works](#how-it-works)
  - [Upgrading from set-hik-ntp.py](#upgrading-from-set-hik-ntppy)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Input File Formats](#input-file-formats)
  - [CSV](#csv)
  - [YAML](#yaml)
  - [JSON](#json)
  - [Optional Fields](#optional-fields)
  - [Selecting Cameras Without a File](#selecting-cameras-without-a-file)
- [Discovering Cameras (`scan`)](#discovering-cameras-scan)
  - [How Discovery Works](#how-discovery-works)
  - [Scan Examples](#scan-examples)
  - [Building a Camera List From a Scan](#building-a-camera-list-from-a-scan)
  - [SADP Multicast Discovery](#sadp-multicast-discovery)
  - [Scan Options](#scan-options)
- [Commands](#commands)
  - [Global Options](#global-options)
  - [NTP](#ntp)
  - [Time and Date](#time-and-date)
  - [Network](#network)
  - [Video](#video)
  - [Recording](#recording)
  - [OSD (On-Screen Display)](#osd-on-screen-display)
  - [Motion Detection](#motion-detection)
  - [Users](#users)
  - [System](#system)
  - [Email Alerts](#email-alerts)
  - [PTZ Presets](#ptz-presets)
  - [Inventory and Raw Requests](#inventory-and-raw-requests)
- [Logging](#logging)
  - [Console Output](#console-output)
  - [Log File](#log-file)
  - [JSON Results](#json-results)
  - [Exit Codes](#exit-codes)
- [Examples](#examples)
- [Safety Notes](#safety-notes)
- [Project Layout](#project-layout)
- [Contributing](#contributing)

---

## Overview

`hik_config.py` reads a list of cameras from a **CSV, YAML or JSON** file, connects to all of them
**in parallel**, and runs one command on each camera, such as `get-video` or `set-ntp`. Each camera's
results are printed as one readable block, followed by a summary of successes and failures.

[↑ Back to Contents](#table-of-contents)

### Features

| Area | Read | Write |
|---|---|---|
| NTP | `get-ntp` | `set-ntp`: server, port, interval, sync on/off |
| Time / Date | `get-time` | `set-time`: manual time, sync to PC clock, timezone, DST |
| Network | `get-network` | `set-network`: static IP / DHCP, netmask, gateway, DNS |
| Video | `get-video` | `set-video`: codec, resolution, fps, bitrate, CBR/VBR, GOP, profile (main/sub/third stream) |
| Recording | `get-recording` | `set-recording`: weekly schedule, recording type, pre/post-record, audio |
| OSD | `get-osd` | `set-osd`: camera name, date/time overlay, formats, positions, custom text |
| Motion detection | `get-motion` | `set-motion`: on/off, sensitivity, highlight, full-frame / clear area |
| Users | `list-users` | `add-user`, `set-password`, `set-user-role`, `delete-user` |
| System | `get-info`, `get-status` | `set-device-name`, `reboot`, `restore-defaults`, `backup-config` |
| Email / SMTP | `get-email` | `set-email`: SMTP server, port, TLS, auth, sender, recipients, motion email on/off |
| PTZ | `get-ptz-presets` | `call-preset`, `set-preset`, `delete-preset` |
| Discovery | `scan` (active probe + SADP) | writes a camera list file |
| Everything | `get-all` | `raw` (any ISAPI request) |

Other features:

- **Camera discovery:** [`scan`](#discovering-cameras-scan) sweeps IPs/subnets (and/or SADP multicast) to find cameras when you don't have a list, and writes one in any supported format.
- **Concurrent:** cameras run in a thread pool (`--workers`, default 8).
- **Readable per-camera logs:** output from different cameras never interleaves. Each run also writes a log file with timestamps and can export results to JSON.
- **Digest and Basic authentication:** `auto` mode tries Digest first and switches to Basic if the camera only offers Basic.
- **Dry run (`--dry-run`):** reads current settings and shows what would change, without writing anything.
- **Lockout protection:** after one `401 Unauthorized`, the tool sends no more requests to that camera, so a wrong password does not lock the account.
- **Firmware tolerant:** settings are changed with read-modify-write (unchanged fields go back exactly as the camera sent them). Time and NTP changes use the minimal "GUI-style" payload that older firmware accepts.

[↑ Back to Contents](#table-of-contents)

### How It Works

1. The camera list is loaded from the file type matching the extension (`.csv`, `.yaml`/`.yml`, `.json`) and/or from `--ip`.
2. Missing usernames and passwords come from `-u` / `-p` / `HIK_PASSWORD`, or you are prompted once.
3. Each camera gets its own `CameraClient` (ISAPI session) and runs in a worker thread.
4. For writes, the tool GETs the current XML, changes only the fields you asked for, and PUTs it back.
5. Every step is logged as `✓` (success) or `✗` (failure with the HTTP status and Hikvision's error code). One failing step or camera never stops the others.
6. At the end you get a summary table, and the exit code shows whether every camera succeeded.

[↑ Back to Contents](#table-of-contents)

### Upgrading from set-hik-ntp.py

The old script had the IP range and NTP server hard-coded at the top. The same task now looks like this:

```bash
# Old: edit BASE_IP / START_IP / END_IP / NTP_SERVER, then run  python3 set-hik-ntp.py
python3 hik_config.py set-ntp --ip 192.168.20.42-60 -u admin \
    --server 192.168.20.230 --ntp-port 123 --interval 60
```

The proven method is unchanged: the camera's own timezone string is reused as-is, `timeMode` is set
to NTP with the minimal GUI-style XML, then `/ISAPI/System/time/ntpServers/1` is updated. The log
file is now `hik-config.log` instead of `hik-ntp-set-log`.

[↑ Back to Contents](#table-of-contents)

---

## Installation

Requires **Python 3.8+** on Linux, macOS or Windows.

```bash
git clone https://github.com/entropytwo-ai/hikvision-cli-set-config-utility.git
cd hikvision-cli-set-config-utility

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

Dependencies (`requirements.txt`):

| Package | Purpose |
|---|---|
| `requests` | HTTP client with Digest/Basic auth |
| `pyyaml` | YAML camera files |
| `defusedxml` | Safe XML parsing of camera responses |
| `colorama` | Coloured terminal output (optional; works without it) |

On Debian/Ubuntu you can use system packages instead of a venv:
`sudo apt install python3-requests python3-yaml python3-defusedxml python3-colorama`.

[↑ Back to Contents](#table-of-contents)

---

## Quick Start

```bash
# 1. Create a camera list (or copy one from examples/)
cp examples/cameras.csv cameras.csv && nano cameras.csv

# 2. Read something harmless first
python3 hik_config.py get-info -c cameras.csv

# 3. Preview a change without applying it
python3 hik_config.py set-ntp -c cameras.csv --server pool.ntp.org --dry-run

# 4. Apply it
python3 hik_config.py set-ntp -c cameras.csv --server pool.ntp.org

# Help
python3 hik_config.py --help
python3 hik_config.py set-video --help
```

[↑ Back to Contents](#table-of-contents)

---

## Input File Formats

The format is chosen by file extension. Every camera needs **`ip`**. `username` and `password` can
be in the file or given on the command line. `port` defaults to `80` (or `443` with HTTPS). Ready-made
samples are in [`examples/`](examples/).

[↑ Back to Contents](#table-of-contents)

### CSV

`cameras.csv`: header row required. Blank lines and lines starting with `#` are ignored.

```csv
ip,username,password,port
192.168.1.100,admin,ChangeMe123!,80
192.168.1.101,admin,ChangeMe123!,80
192.168.1.102,admin,ChangeMe123!,8080
```

[↑ Back to Contents](#table-of-contents)

### YAML

`cameras.yaml`: a list of mappings. A top-level `cameras:` key is also accepted.

```yaml
- ip: 192.168.1.100
  username: admin
  password: "ChangeMe123!"
  port: 80

- ip: 192.168.1.101
  username: admin
  password: "ChangeMe123!"
  port: 80
```

[↑ Back to Contents](#table-of-contents)

### JSON

`cameras.json`: an array of objects, or `{"cameras": [...]}`.

```json
[
  {"ip": "192.168.1.100", "username": "admin", "password": "ChangeMe123!", "port": 80},
  {"ip": "192.168.1.101", "username": "admin", "password": "ChangeMe123!", "port": 80}
]
```

[↑ Back to Contents](#table-of-contents)

### Optional Fields

These work in all three formats:

| Field | Default | Description |
|---|---|---|
| `port` | 80 / 443 | HTTP(S) port |
| `name` | (none) | Friendly label shown in logs; also available as `{name}` in `set-osd` / `set-device-name` |
| `https` | `false` | Use HTTPS for this camera |
| `auth` | `auto` | `auto`, `digest` or `basic` |
| `channel` | `1` | Video channel (for NVRs / multi-channel encoders) |
| `new_ip`, `new_netmask`, `new_gateway` | (none) | Per-camera targets for `set-network` |

If `username`/`password` are left out, the values from `-u`/`-p`/`HIK_PASSWORD` are used. If those are
also missing, you are prompted once at startup. Keep camera files out of version control
(`.gitignore` already excludes `cameras.*` in the repo root).

[↑ Back to Contents](#table-of-contents)

### Selecting Cameras Without a File

```bash
python3 hik_config.py get-info --ip 192.168.1.100 -u admin                 # single camera (password prompted)
python3 hik_config.py get-info --ip 192.168.1.100-120 -u admin             # last-octet range
python3 hik_config.py get-info --ip 192.168.1.10-192.168.2.20 -u admin     # full range
python3 hik_config.py get-info --ip 10.0.5.0/27 -u admin                   # CIDR
python3 hik_config.py get-info -c cameras.yaml --ip 10.0.9.5 -u admin      # file + extra IPs
```

[↑ Back to Contents](#table-of-contents)

---

## Discovering Cameras (`scan`)

Don't have a camera list, or don't know which IPs your cameras use? The `scan`
command sweeps one or more IPs/subnets, identifies every ISAPI-compatible camera
(Hikvision and the many OEM/clone brands that speak the same protocol), and can
write the results straight into a ready-to-use camera list file.

```bash
# Scan a whole /24 and save everything it finds as a YAML camera list
python3 hik_config.py scan --ip 192.168.1.0/24 -o cameras.yaml

# Scan a range on ports 80 and 443, log in to read model/firmware, save as CSV
python3 hik_config.py scan --ip 192.168.1.10-50 --ports 80,443 \
    -u admin -p 'MyPassword!' -o cameras.csv
```

[↑ Back to Contents](#table-of-contents)

### How Discovery Works

For every `IP:port` target, `scan`:

1. Does a quick **TCP connect** to see whether the port is open (closed hosts are
   skipped fast, so scanning a full subnet is quick).
2. Requests **`/ISAPI/System/deviceInfo`** without credentials:
   - **HTTP 200** with a valid `DeviceInfo` document → an **open camera**; the
     model, serial, firmware, MAC and name are read directly.
   - **HTTP 401** → a **credential-protected ISAPI camera**. It is recorded as a
     camera that *needs login*. If you supply `-u/-p`, `scan` authenticates and
     reads its details too.
   - **any other response** → a web server that is **not** an ISAPI camera (listed
     only with `--show-all`).
3. Classifies the vendor from the model prefix (`DS-` → Hikvision, etc.) or the
   `Server:`/auth realm when the model isn't readable.

Each host prints a single line as it is probed, and a summary is shown at the end:

```
════════════════════════════════════════════════════════════
SCAN  (254 target(s))
════════════════════════════════════════════════════════════
  ✓ 192.168.1.64  DS-2CD2143G2-I fw V5.7.3 "Front Door"
  ✓ 192.168.1.65  DS-2CD2347G2 fw V5.7.3 "Driveway"
  ! 192.168.1.70  ISAPI device - authentication required (supply -u/-p to read details)
  • 192.168.1.1   HTTP 200 but not an ISAPI deviceInfo (Server: lighttpd)

════════════════════════════════════════════════════════════
SCAN SUMMARY
════════════════════════════════════════════════════════════
  Targets probed    : 254 (254 host(s) x 1 port(s))
  Cameras found     : 3
    - readable       : 2
    - need login     : 1
  Other open hosts  : 1
  Camera list       : cameras.yaml
════════════════════════════════════════════════════════════
```

> **Scope / etiquette:** `scan` probes only the exact addresses you give it, with
> one ordinary ISAPI request per host — it is a device-discovery helper for *your
> own* network, not a stealth port scanner. Only scan networks you are authorised
> to.

[↑ Back to Contents](#table-of-contents)

### Scan Examples

```bash
# Single IP, is it a camera?
python3 hik_config.py scan --ip 192.168.1.64

# A range, shorthand end (.10 - .50)
python3 hik_config.py scan --ip 192.168.1.10-50

# Several subnets at once, non-standard ports
python3 hik_config.py scan --ip 10.0.1.0/24 --ip 10.0.2.0/24 --ports 80,8000

# Authenticate so model/firmware/serial are filled in
python3 hik_config.py scan --ip 192.168.1.0/24 -u admin -p 'MyPassword!'

# HTTPS cameras on 443
python3 hik_config.py scan --ip 192.168.1.0/24 --https --ports 443

# Also list closed/unreachable hosts and dump a full JSON report
python3 hik_config.py scan --ip 192.168.1.0/24 --show-all --json-out scan-report.json
```

[↑ Back to Contents](#table-of-contents)

### Building a Camera List From a Scan

Add `-o FILE` and the discovered cameras are written as a camera list in the format
chosen by the file extension (`.csv`, `.yaml`/`.yml` or `.json`). The file loads
straight back into every other command:

```bash
# 1. Discover and save (model/serial/firmware included as extra columns)
python3 hik_config.py scan --ip 192.168.1.0/24 -u admin -p 'MyPassword!' -o cameras.yaml

# 2. Use the generated list immediately
python3 hik_config.py get-all   -c cameras.yaml -p 'MyPassword!'
python3 hik_config.py set-ntp    -c cameras.yaml -p 'MyPassword!' --server pool.ntp.org
```

Notes:

- Only devices identified as cameras are written (plain web servers are skipped).
- `-u/--username` is saved in the file; the **password is not** unless you add
  `--save-credentials` (otherwise pass `-p` when you run later commands, or set
  `HIK_PASSWORD`).
- Add `--no-meta` to omit the `vendor`/`model`/`serial`/`firmware`/`mac` columns
  and keep the file minimal.

[↑ Back to Contents](#table-of-contents)

### SADP Multicast Discovery

`--sadp` additionally runs Hikvision's **SADP** discovery — the same multicast
(`239.255.255.250:37020`) mechanism the official SADP tool uses. It finds devices
even when their IP is unknown or mis-configured (e.g. a brand-new camera still on
the wrong subnet), **but only on the local layer-2 segment** (multicast is not
routed, so it cannot see cameras behind a router or VPN).

```bash
# Multicast discovery only
python3 hik_config.py scan --sadp

# Combine: multicast + an active probe of a subnet, merged into one list
python3 hik_config.py scan --sadp --ip 192.168.1.0/24 -u admin -p 'MyPassword!' -o cameras.json
```

If the SADP UDP port is unavailable (e.g. the official SADP tool is already
running, or the platform blocks it) the scan reports it and continues with the
active probe.

[↑ Back to Contents](#table-of-contents)

### Scan Options

| Option | Description |
| --- | --- |
| `--ip IP\|RANGE\|CIDR` | Target(s): single IP, range (`.10-50` or full), or CIDR. Repeatable. |
| `--ports LIST` | Port(s) to probe, e.g. `80,443,8000-8010` (default `80`). |
| `-u`, `-p` | Credentials — optional; enable reading model/firmware of protected cameras. |
| `--auth {auto,digest,basic}` | HTTP auth method (default `auto`). |
| `--https` / `--verify-tls` | Probe over HTTPS / verify certificates. |
| `--timeout` / `--tcp-timeout` | Per-host HTTP timeout / TCP pre-check timeout (seconds). |
| `-w`, `--workers` | Hosts probed in parallel (default `32`). |
| `-o`, `--out FILE` | Write discovered cameras to `.csv`/`.yaml`/`.json`. |
| `--save-credentials` | Include the password in the written file. |
| `--no-meta` | Omit model/serial/firmware/mac columns. |
| `--json-out FILE` | Write the full scan report (all hosts) as JSON. |
| `--show-all` | Also list closed/unreachable and non-camera hosts. |
| `--sadp` / `--sadp-timeout` | Also run SADP multicast discovery / listen time (seconds). |
| `--no-color`, `--log-file`, `--no-log-file` | Output/logging controls (as elsewhere). |

[↑ Back to Contents](#table-of-contents)

---

## Commands

General form:

```
python3 hik_config.py <command> [camera selection] [global options] [command options]
```

Every command accepts the [global options](#global-options). Every `set-*` command needs at least one
setting to change, and stops before contacting any camera if none is given.

[↑ Back to Contents](#table-of-contents)

### Global Options

| Option | Description |
|---|---|
| `-c, --cameras FILE` | Camera list (`.csv`, `.yaml`, `.yml`, `.json`) |
| `--ip IP\|RANGE\|CIDR` | Add cameras directly (repeatable) |
| `-u, --username` / `-p, --password` | Credentials for cameras without them (`HIK_PASSWORD` env var also works) |
| `--port` | Port for `--ip` cameras |
| `--https`, `--verify-tls` | Use HTTPS / verify certificates (off by default: cameras use self-signed certificates) |
| `--auth auto\|digest\|basic` | HTTP authentication (default `auto`) |
| `--timeout SEC` | Request timeout (default 5) |
| `-w, --workers N` | Cameras processed in parallel (default 8) |
| `--dry-run` | Read only; show what would change |
| `-y, --yes` | Skip the confirmation prompt for `reboot`, `restore-defaults`, `set-network`, `delete-user` |
| `--log-file FILE` / `--no-log-file` | Log file (default `hik-config.log`) |
| `--json-out FILE` | Write all results and messages as JSON |
| `-q, --quiet` | Print only cameras that had failures (the summary is always printed) |
| `--no-color` | Plain output (colour is also turned off automatically when output is piped) |

[↑ Back to Contents](#table-of-contents)

### NTP

ISAPI: `/ISAPI/System/time`, `/ISAPI/System/time/ntpServers/1`

| Command | Options |
|---|---|
| `get-ntp` | `--server-id N` |
| `set-ntp` | `--server HOST\|IP`, `--ntp-port N` (default 123), `--interval MIN` (default 60), `--sync on\|off`, `--server-id N` |

Setting `--server` also turns NTP sync on, unless you add `--sync off`. `--sync off` on its own
switches the camera to manual time.

```bash
python3 hik_config.py get-ntp -c cameras.csv
python3 hik_config.py set-ntp -c cameras.csv --server 192.168.20.230 --interval 60
python3 hik_config.py set-ntp -c cameras.csv --server pool.ntp.org --ntp-port 123
python3 hik_config.py set-ntp -c cameras.csv --sync off
```

[↑ Back to Contents](#table-of-contents)

### Time and Date

ISAPI: `/ISAPI/System/time`

| Command | Options |
|---|---|
| `get-time` | (none). Shows time mode, device time, timezone, UTC offset and DST rule |
| `set-time` | `--mode manual\|ntp`, `--manual-time YYYY-MM-DDTHH:MM:SS`, `--sync-to-host`, `--timezone TZ`, `--tz-preset NAME`, `--dst on\|off`, `--dst-name`, `--dst-start RULE`, `--dst-end RULE` |

Hikvision keeps the timezone and the DST rule in one POSIX-style string with an **inverted sign**,
e.g. `EST5:00:00EDT,M3.2.0/02:00:00,M11.1.0/02:00:00` is US Eastern with DST, and `CST-8:00:00` is
UTC+8. Presets: `UTC`, `US-Eastern`, `US-Central`, `US-Mountain`, `US-Arizona`, `US-Pacific`,
`US-Alaska`, `US-Hawaii`, `UK`, `Europe-Central`, `Europe-Eastern`, `India`, `China`, `Japan`,
`Australia-Sydney`.

```bash
python3 hik_config.py get-time -c cameras.yaml
python3 hik_config.py set-time -c cameras.yaml --tz-preset US-Eastern
python3 hik_config.py set-time -c cameras.yaml --sync-to-host            # copy this PC's clock
python3 hik_config.py set-time -c cameras.yaml --manual-time 2026-10-29T14:30:00
python3 hik_config.py set-time -c cameras.yaml --dst off
python3 hik_config.py set-time -c cameras.yaml --dst on --dst-name EDT --dst-start M3.2.0/02:00:00 --dst-end M11.1.0/02:00:00
python3 hik_config.py set-time -c cameras.yaml --timezone "CET-1:00:00CEST,M3.5.0/02:00:00,M10.5.0/03:00:00"
```

[↑ Back to Contents](#table-of-contents)

### Network

ISAPI: `/ISAPI/System/Network/interfaces/<id>/ipAddress`

| Command | Options |
|---|---|
| `get-network` | `--interface N`. Shows IP, mask, gateway, DNS, DHCP, MAC, MTU |
| `set-network` | `--dhcp` \| `--static`, `--new-ip IP` (single camera only), `--netmask`, `--gateway`, `--dns1`, `--dns2`, `--interface N` |

To give several cameras different static IPs, add `new_ip` (and optionally `new_netmask`,
`new_gateway`) to the camera file. `set-network` asks for confirmation unless `-y` is given.

```bash
python3 hik_config.py get-network -c cameras.csv
python3 hik_config.py set-network -c cameras.csv --dns1 1.1.1.1 --dns2 8.8.8.8
python3 hik_config.py set-network --ip 192.168.1.64 -u admin --new-ip 192.168.1.120 --netmask 255.255.255.0 --gateway 192.168.1.1
python3 hik_config.py set-network -c readdress.csv --static      # uses new_ip per row
python3 hik_config.py set-network -c cameras.csv --dhcp -y
```

`readdress.csv`:

```csv
ip,username,password,port,new_ip,new_gateway
192.168.1.64,admin,ChangeMe123!,80,192.168.1.120,192.168.1.1
192.168.1.65,admin,ChangeMe123!,80,192.168.1.121,192.168.1.1
```

[↑ Back to Contents](#table-of-contents)

### Video

ISAPI: `/ISAPI/Streaming/channels/<channel><stream>` (101 = main, 102 = sub, 103 = third)

| Command | Options |
|---|---|
| `get-video` | `--stream main\|sub\|third` (repeatable; default main + sub), `--channel N` |
| `set-video` | `--stream` (repeatable; default main), `--codec h264\|h265\|mjpeg`, `--resolution WxH`, `--fps N`, `--bitrate-mode CBR\|VBR`, `--bitrate KBPS`, `--quality 1-100`, `--gop N`, `--profile baseline\|main\|high`, `--enabled on\|off`, `--channel N` |

The camera checks each value against what it supports. If it rejects one (for example a resolution the
sub stream cannot do), that camera's log shows the error and the other cameras continue.

```bash
python3 hik_config.py get-video -c cameras.json
python3 hik_config.py get-video -c cameras.json --stream third
python3 hik_config.py set-video -c cameras.json --codec h265 --resolution 2560x1440 --fps 20
python3 hik_config.py set-video -c cameras.json --bitrate-mode VBR --bitrate 4096 --quality 80
python3 hik_config.py set-video -c cameras.json --stream sub --resolution 640x480 --fps 15 --bitrate-mode CBR --bitrate 512
python3 hik_config.py set-video -c cameras.json --stream main --stream sub --gop 50
```

[↑ Back to Contents](#table-of-contents)

### Recording

ISAPI: `/ISAPI/ContentMgmt/record/tracks/<id>` (for cameras recording to SD card / NAS; for NVR recording, point the tool at the NVR)

| Command | Options |
|---|---|
| `get-recording` | `--stream main\|sub`, `--channel N` |
| `set-recording` | `--enable on\|off`, `--mode continuous\|motion\|alarm\|motion-or-alarm\|motion-and-alarm\|event`, `--days all\|weekdays\|weekends\|Mon,Wed,…`, `--start HH:MM`, `--end HH:MM`, `--pre-record SEC`, `--post-record SEC`, `--audio on\|off`, `--stream`, `--channel` |

`--mode` **replaces** the whole weekly schedule with one time window per selected day.

```bash
python3 hik_config.py get-recording -c cameras.csv
python3 hik_config.py set-recording -c cameras.csv --mode continuous --days all
python3 hik_config.py set-recording -c cameras.csv --mode motion --days weekdays --start 08:00 --end 18:00
python3 hik_config.py set-recording -c cameras.csv --pre-record 5 --post-record 30
python3 hik_config.py set-recording -c cameras.csv --enable off
```

[↑ Back to Contents](#table-of-contents)

### OSD (On-Screen Display)

ISAPI: `/ISAPI/System/Video/inputs/channels/<ch>` (name), `…/overlays`, `…/overlays/text/<id>`

| Command | Options |
|---|---|
| `get-osd` | `--channel N` |
| `set-osd` | `--channel-name TEXT`, `--show-name on\|off`, `--name-pos X,Y`, `--datetime on\|off`, `--datetime-pos X,Y`, `--date-style`, `--time-style 12hour\|24hour`, `--display-week on\|off`, `--text TEXT`, `--text-id 1-8`, `--text-enabled on\|off`, `--text-pos X,Y` |

`--channel-name` accepts the placeholders `{ip}` and `{name}`. Positions use Hikvision's 704×576 grid.

```bash
python3 hik_config.py get-osd -c cameras.yaml
python3 hik_config.py set-osd -c cameras.yaml --channel-name "{name}" --show-name on
python3 hik_config.py set-osd -c cameras.yaml --datetime on --date-style YYYY-MM-DD --time-style 24hour --display-week off
python3 hik_config.py set-osd -c cameras.yaml --datetime-pos 16,16 --name-pos 16,540
python3 hik_config.py set-osd -c cameras.yaml --text "Property of ACME" --text-id 1 --text-pos 16,500
python3 hik_config.py set-osd -c cameras.yaml --text-id 1 --text-enabled off
```

[↑ Back to Contents](#table-of-contents)

### Motion Detection

ISAPI: `/ISAPI/System/Video/inputs/channels/<ch>/motionDetection`

| Command | Options |
|---|---|
| `get-motion` | `--channel N`. Shows status, sensitivity, grid size, active area %, regions, linked actions |
| `set-motion` | `--enable on\|off`, `--sensitivity 0-100`, `--highlight on\|off`, `--full-frame` \| `--clear-area`, `--channel N` |

```bash
python3 hik_config.py get-motion -c cameras.csv
python3 hik_config.py set-motion -c cameras.csv --enable on --sensitivity 60 --full-frame
python3 hik_config.py set-motion -c cameras.csv --enable off
```

[↑ Back to Contents](#table-of-contents)

### Users

ISAPI: `/ISAPI/Security/users`, `/ISAPI/Security/users/<id>`

| Command | Options |
|---|---|
| `list-users` | (none) |
| `add-user` | `--user NAME`, `--new-password` (prompted if omitted), `--role administrator\|operator\|viewer` |
| `set-password` | `--user NAME` (default: the login user of each camera), `--new-password` (prompted if omitted) |
| `set-user-role` | `--user NAME`, `--role …` |
| `delete-user` | `--user NAME` (the built-in `admin` and the login account cannot be deleted) |

Newer firmware wants the current admin password in `<loginPassword>`. The tool retries with it
automatically when the camera asks. Hikvision requires passwords of at least 8 characters. Leave out
`--new-password` so the password is prompted for, rather than saved in your shell history.

```bash
python3 hik_config.py list-users -c cameras.csv
python3 hik_config.py add-user -c cameras.csv --user nvr --role operator
python3 hik_config.py set-password -c cameras.csv --user nvr
python3 hik_config.py set-user-role -c cameras.csv --user nvr --role viewer
python3 hik_config.py delete-user -c cameras.csv --user olduser -y
```

[↑ Back to Contents](#table-of-contents)

### System

ISAPI: `/ISAPI/System/deviceInfo`, `/ISAPI/System/status`, `/ISAPI/System/reboot`, `/ISAPI/System/factoryReset`, `/ISAPI/System/configurationData`

| Command | Options |
|---|---|
| `get-info` | (none). Shows model, serial, MAC, firmware version/date, device name |
| `get-status` | (none). Shows uptime, CPU, memory |
| `set-device-name` | `--name TEXT` (placeholders `{ip}`, `{name}`) |
| `reboot` | (none). Asks for confirmation unless `-y` |
| `restore-defaults` | `--confirm` (**required**), `--mode basic\|full` (default `basic` keeps network settings and users) |
| `backup-config` | `--output-dir DIR` (default `backups/`). Saves one `.bin` file per camera |

```bash
python3 hik_config.py get-info -c cameras.csv --json-out inventory.json
python3 hik_config.py get-status -c cameras.csv
python3 hik_config.py set-device-name -c cameras.csv --name "CAM-{name}"
python3 hik_config.py backup-config -c cameras.csv --output-dir backups/2026-10-29
python3 hik_config.py reboot -c cameras.csv -y
python3 hik_config.py restore-defaults --ip 192.168.1.100 -u admin --confirm --mode basic
```

[↑ Back to Contents](#table-of-contents)

### Email Alerts

ISAPI: `/ISAPI/System/Network/mailing`, `/ISAPI/Event/triggers/VMD-<ch>`

| Command | Options |
|---|---|
| `get-email` | `--channel N`. Shows SMTP settings, recipients, whether motion events send email |
| `set-email` | `--server`, `--smtp-port`, `--sender`, `--sender-name`, `--smtp-user` (`''` turns SMTP auth off), `--smtp-password`, `--ssl on\|off`, `--starttls on\|off`, `--recipient EMAIL` (repeatable; replaces the list), `--attach-snapshot on\|off`, `--enable on\|off` (email on motion), `--channel N` |

```bash
python3 hik_config.py get-email -c cameras.csv
python3 hik_config.py set-email -c cameras.csv --server smtp.example.com --smtp-port 587 --starttls on \
    --smtp-user alerts@example.com --smtp-password 'S3cret!' --sender alerts@example.com \
    --recipient ops@example.com --recipient oncall@example.com --attach-snapshot on --enable on
python3 hik_config.py set-email -c cameras.csv --enable off
```

[↑ Back to Contents](#table-of-contents)

### PTZ Presets

ISAPI: `/ISAPI/PTZCtrl/channels/<ch>/presets`. Cameras without PTZ report `403 … notSupport`, and the
tool logs that as a failure for those cameras.

| Command | Options |
|---|---|
| `get-ptz-presets` | `--channel N` |
| `call-preset` | `--preset ID\|NAME`, `--channel N` |
| `set-preset` | `--id N`, `--name TEXT`, `--channel N`. Saves the camera's **current** position |
| `delete-preset` | `--preset ID\|NAME`, `--channel N` |

```bash
python3 hik_config.py get-ptz-presets -c ptz.yaml
python3 hik_config.py call-preset -c ptz.yaml --preset "Gate"
python3 hik_config.py set-preset --ip 192.168.1.150 -u admin --id 5 --name "Loading Dock"
python3 hik_config.py delete-preset --ip 192.168.1.150 -u admin --preset 5
```

[↑ Back to Contents](#table-of-contents)

### Inventory and Raw Requests

| Command | Description |
|---|---|
| `get-all` | Runs every read-only check (info, time, NTP, network, video, recording, OSD, motion, email, users) |
| `raw` | `--method GET\|PUT\|POST\|DELETE --path /ISAPI/... [--data-file body.xml]` sends any ISAPI request |

```bash
python3 hik_config.py get-all -c cameras.csv --json-out full-inventory.json
python3 hik_config.py raw -c cameras.csv --path /ISAPI/System/capabilities
python3 hik_config.py raw --ip 192.168.1.100 -u admin --method PUT --path /ISAPI/Image/channels/1/irCutFilter --data-file ircut.xml
```

[↑ Back to Contents](#table-of-contents)

---

## Logging

[↑ Back to Contents](#table-of-contents)

### Console Output

Each camera's messages are buffered and printed **as one block when that camera finishes**, so
parallel cameras never mix their lines. `✓` means success, `✗` means failure (with the HTTP status and
Hikvision's own error string), `!` is a warning and `•` is information.

```
────────────────────────────────────────────────────────────
Camera: 192.168.1.100 [Lobby] (admin)
────────────────────────────────────────────────────────────
  ✓ Time mode set to: NTP (NTP sync enabled)
  ✓ NTP server set to: pool.ntp.org:123 (sync every 60 min)
────────────────────────────────────────────────────────────
────────────────────────────────────────────────────────────
Camera: 192.168.1.101 (admin)
────────────────────────────────────────────────────────────
  ✓ Video config (main stream) retrieved successfully
  ✗ Failed to set main stream video config: 403 Forbidden (Invalid Operation / notSupport)
────────────────────────────────────────────────────────────
────────────────────────────────────────────────────────────
Camera: 192.168.1.102 (admin)
────────────────────────────────────────────────────────────
  ✗ Failed to read current timezone: Connection timed out
────────────────────────────────────────────────────────────

════════════════════════════════════════════════════════════
SUMMARY  (set-ntp)
════════════════════════════════════════════════════════════
  ✓ OK      192.168.1.100 [Lobby]   2 succeeded, 0 failed
  ✗ FAILED  192.168.1.101           1 succeeded, 1 failed
  ✗ FAILED  192.168.1.102           0 succeeded, 1 failed
────────────────────────────────────────────────────────────
  Total cameras : 3
  Successful    : 1
  Failed        : 2
  Elapsed       : 5.2s
  Log file      : hik-config.log
════════════════════════════════════════════════════════════
```

Read commands also print the values under each `✓` line, indented as `key : value`.

[↑ Back to Contents](#table-of-contents)

### Log File

Every run is appended to `hik-config.log` (change it with `--log-file`, turn it off with
`--no-log-file`). Each line has a timestamp and the camera, so you can `grep` it:

```
2026-10-29 22:26:51 === hik-config 'set-ntp' started on 3 camera(s) ===
2026-10-29 22:26:51 [192.168.1.100 [Lobby]] ✓ Time mode set to: NTP (NTP sync enabled)
2026-10-29 22:26:51 [192.168.1.100 [Lobby]] ✓ NTP server set to: pool.ntp.org:123 (sync every 60 min)
2026-10-29 22:26:56 [192.168.1.102] ✗ Failed to read current timezone: Connection timed out
2026-10-29 22:26:56 === hik-config 'set-ntp' finished: 1 ok, 2 failed ===
```

```bash
grep "✗" hik-config.log          # all failures
grep "192.168.1.100" hik-config.log
```

[↑ Back to Contents](#table-of-contents)

### JSON Results

`--json-out FILE` writes a machine-readable report: totals, and for each camera its success flag, every
message, and all values read. Useful for inventories and spreadsheets:

```json
{
  "command": "get-info", "total": 3, "successes": 3, "failures": 0,
  "cameras": [
    {"ip": "192.168.1.100", "port": 80, "name": "Lobby", "success": true,
     "messages": [{"time": "2026-10-29T22:26:51", "level": "ok", "text": "Device info retrieved successfully"}],
     "results": {"device_info": {"model": "DS-2CD2143G2-I", "firmware_version": "V5.7.3", "...": "..."}}}
  ]
}
```

[↑ Back to Contents](#table-of-contents)

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | Every camera succeeded |
| `1` | At least one camera had a failure (or the run was aborted at the confirmation prompt) |
| `2` | Usage error (bad options, unreadable camera file, nothing to change) |
| `130` | Interrupted with Ctrl+C |

[↑ Back to Contents](#table-of-contents)

---

## Examples

```bash
# Inventory all cameras to JSON
python3 hik_config.py get-info -c cameras.csv --json-out inventory.json

# Standardise time on a site: NTP server + US Eastern with DST
python3 hik_config.py set-time -c site-a.yaml --tz-preset US-Eastern
python3 hik_config.py set-ntp  -c site-a.yaml --server 10.10.0.1 --interval 60

# Bandwidth reduction: H.265, 15 fps, 2 Mbps CBR on main streams, 50 cameras at a time
python3 hik_config.py set-video -c cameras.json -w 50 --codec h265 --fps 15 --bitrate-mode CBR --bitrate 2048

# Name every camera after its file entry and show that name on screen
python3 hik_config.py set-device-name -c cameras.yaml --name "{name}"
python3 hik_config.py set-osd -c cameras.yaml --channel-name "{name}" --show-name on

# Motion recording during business hours on weekdays
python3 hik_config.py set-recording -c cameras.csv --mode motion --days weekdays --start 07:00 --end 19:00

# Rotate the admin password everywhere (prompted, never echoed)
python3 hik_config.py set-password -c cameras.csv --user admin

# Back up configs before a big change, then preview the change
python3 hik_config.py backup-config -c cameras.csv
python3 hik_config.py set-motion -c cameras.csv --enable on --sensitivity 50 --dry-run

# Only show problems; keep a per-run log file
python3 hik_config.py get-all -c cameras.csv -q --log-file "logs/$(date +%F).log"

# Old firmware that only supports Basic auth over HTTPS on a custom port
python3 hik_config.py get-info --ip 10.1.1.20 --port 8443 --https --auth basic -u admin
```

[↑ Back to Contents](#table-of-contents)

---

## Safety Notes

- **Account lockout:** Hikvision locks an account after several failed logins. After one `401`, this tool stops contacting that camera for the rest of the run. Test new credentials on a single camera first.
- **Disruptive commands:** `reboot`, `restore-defaults`, `set-network` and `delete-user` ask for confirmation, or need `-y`, which non-interactive runs (cron, CI) must pass. `restore-defaults` also always needs `--confirm`.
- **Use `--dry-run` first:** it performs every read but sends no PUT, POST or DELETE.
- **Network changes:** changing the IP or turning on DHCP drops the connection. Make sure the new address is reachable from your machine.
- **Credentials:** use `-p`-less runs (prompt) or `HIK_PASSWORD` so passwords stay out of shell history. Camera files contain passwords, so protect them (`chmod 600 cameras.csv`).
- **Firmware differences:** ISAPI support varies by model and firmware. Unsupported features show up as clear per-camera failures (`notSupport`, `404`) and never stop the run.

[↑ Back to Contents](#table-of-contents)

---

## Project Layout

```
hik_config.py              CLI entry point (argparse subcommands, camera selection, confirmation)
modules/
  camera_client.py         CameraClient: ISAPI session, Digest/Basic auth, errors, XML helpers
  camera_loader.py         CSV / YAML / JSON camera loader + writer, IP ranges
  discovery.py             Network scan: TCP/ISAPI probe + SADP multicast discovery
  worker.py                ThreadPoolExecutor runner, per-camera log blocks, summary, JSON export
  common.py                argparse helpers shared by modules
  ntp.py  time_date.py  network.py  video.py  recording.py  osd.py
  motion.py  users.py  system.py  email_alerts.py  ptz.py
examples/                  cameras.csv / cameras.yaml / cameras.json samples
tests/
  mock_camera.py           In-memory fake ISAPI camera (Digest or Basic auth) for offline testing
  test_hik_config.py       Unit and end-to-end tests against the mock
```

[↑ Back to Contents](#table-of-contents)

---

## Contributing

Contributions are welcome: new ISAPI areas, firmware quirks, bug reports.

1. Fork the repo and create a feature branch: `git checkout -b feature/my-change`.
2. Install the dependencies (`pip install -r requirements.txt`).
3. **Adding a feature module:** create `modules/<area>.py` with
   - a class that wraps the ISAPI calls (use `client.get_xml()` / `client.put_xml()` and the `text()` / `set_text()` helpers; prefer read-modify-write),
   - `cmd_*(client, log, args, camera)` handlers that wrap each step in `with log.step("do something"):` and report with `log.ok()` / `log.data()`,
   - a `register(sub, parents)` function that adds the subcommands with `add_command(...)`.

   Then add the module to `FEATURE_MODULES` in `hik_config.py`, and add its getter to `GET_ALL` if it is read-only.
4. Add a fixture for the new endpoint to `tests/mock_camera.py` and a test to `tests/test_hik_config.py`.
5. Run the tests. They need no camera:

   ```bash
   python3 -m unittest discover -s tests -v
   # or try the CLI against a fake camera:
   python3 tests/mock_camera.py --port 8081 &
   python3 hik_config.py get-all --ip 127.0.0.1 --port 8081 -u admin -p 'Passw0rd!'
   ```

6. Update this README (keep the table of contents and the "Back to Contents" links), then open a pull request describing the camera models and firmware you tested.

Licensed under the GNU GPL v3. See [LICENSE](LICENSE).

[↑ Back to Contents](#table-of-contents)
