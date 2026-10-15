# CloudEdge / Meari — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Home Assistant custom integration for battery-powered Wi-Fi cameras built on
the Meari cloud platform.

Meari is the underlying cloud and device platform; CloudEdge is one of its
main consumer-facing apps and services. The integration communicates directly
with the platform and cameras using the VVP / PPStrong P2P video protocol — no
third-party bridge, no Frigate plugin, and no extra container are required.

> ⚠️ **Unofficial.** Not affiliated with CloudEdge, Meari, or any reseller.
> Use at your own risk; cloud APIs can change without notice.

---

## Supported apps

The account profile must match the app used to register the account:

- CloudEdge
- CloudPlus / CloudHome
- ANRAN
- Arenti
- BoifunCam
- ieGeek

These apps use the same underlying Meari platform, but the server requires
each app's own identity during login.

---

## Highlights

- **Live MPEG-TS stream** served by HA itself — video is **copied** from
  the camera with no transcoding, so the stream carries whatever the camera
  natively produces:
  - **H.264** on the lower profiles (typically `SD`)
  - **HEVC / H.265** on the higher profiles (typically `HD`, `QHD`, `AUTO`)

  Audio (G.711 µ-law) is the only thing re-encoded, into AAC, so the result
  is a clean MPEG-TS that Frigate, go2rtc, the native HA `stream`
  component, ONVIF clients and most NVRs all accept directly.
- **Idle stream for battery cameras ("snap" cameras)** — while the camera
  is asleep, the integration keeps publishing a still-frame MPEG-TS with
  the same advertised codec / resolution / FPS as the live stream. The
  consumer (Frigate, go2rtc, mobile companion, …) **never sees the source
  disappear**, and the moment the camera wakes up the stream switches to
  live without a reconnect or a "stream lost" event.
- **Motion / AI alerts via MQTT** — battery cameras push PIR, person, pet,
  package and visitor events in real time through the Meari IoT broker.
- **Battery-camera wake control** — manual wake button, auto-wake on motion
  switch, and configurable wake duration.
- **Full settings surface** — most camera IoT toggles (LED, PIR, sirens,
  day/night, recording, anti-flicker, ONVIF, HomeKit, …) appear as native
  HA entities and write back to the camera.
- **PTZ** — `cloudplus.ptz` service for cameras that support it.
- **Multi-camera & multi-account** — one integration entry per account,
  with each camera as a sub-entry (its own device), all created
  automatically after login.

---

## Requirements

- Home Assistant **2025.3+** (camera sub-entries; WebRTC live view needs
  HA's built-in go2rtc, part of the default config)
- A Meari-compatible app account with at least one camera already paired in
  the official app
- `ffmpeg` (bundled in HAOS, HA Container, and HA Supervised)
- Outbound internet access to the Meari cloud (HTTPS + MQTT/TLS) and UDP/TCP
  to the camera (LAN or TURN-relayed)

`pycryptodome` and `paho-mqtt` are pulled in automatically by HACS / pip.

---

## Installation

### HACS (recommended)

1. Open **HACS → Integrations**.
2. Three-dot menu → **Custom repositories** → paste this repo URL → category
   **Integration**.
3. Search for *CloudEdge / CloudPlus / Meari*, install, **restart HA**.

### Manual

1. Copy [custom_components/cloudplus/](custom_components/cloudplus/) into
   `<config>/custom_components/`.
2. Restart Home Assistant.

---

## Setup

1. **Settings → Devices & Services → Add Integration**.
2. Pick *CloudEdge / CloudPlus / Meari*.
3. Enter your account credentials:

   | Field         | Notes |
   |---------------|-------|
   | Email         | Same address you log into the mobile app with. |
   | Password      | Account password. |
   | Country code  | e.g. `FR`, `US`, `DE` — matches your app region. |
   | Phone code    | International dial code (e.g. `33` for France). |
   | App profile   | Choose the matching profile from [Supported apps](#supported-apps). |

4. All cameras on the account are discovered and added automatically. The
   integration page shows **one entry per account**, with each camera
   listed under it as a sub-entry that has its own device.

> 💡 **Use a secondary account.** The Meari MQTT broker only allows one live
> session per account at a time. If Home Assistant logs in with the same
> account as your phone, motion events on the phone may stop arriving (and
> vice-versa). Create a second cloud account and share the camera(s) with it
> (or add it to the "family / home") before adding it to HA.

### Managing cameras

On the integration page, each camera is a sub-entry under its account:

- **Video password** — open the camera sub-entry's ⋮ menu → **Reconfigure**.
  If you enabled **E2EE / video encryption** in the app, enter that 6-digit
  code so the stream can be decrypted; leave it empty otherwise.
- **Remove a camera** — ⋮ → **Delete** on its sub-entry. It stays removed
  across restarts.
- **Add a camera back** (or one HA hasn't picked up) — **Add camera** on the
  account entry lists the account's cameras that aren't added yet.
- **Pause a camera without deleting it** — disable its device. The
  integration stops connecting to it until you enable the device again.

New cameras paired to the account later are added automatically the next
time the integration loads (e.g. after a restart).

### Per-account options

Open the account entry → **Configure** — update the password / country /
phone code / app profile in place. Every camera on the account picks up
the change.

### Upgrading from 0.3.x

Earlier versions created a separate integration entry for every camera next
to the account entry. On first start after upgrading, each camera entry is
folded into its account as a sub-entry: devices, entity IDs, history,
areas, the video password and the stream settings all carry over, and the
old camera entries disappear. A camera whose entry you had disabled comes
back as a disabled device.

---

## Entities

Every camera shows a fixed core set of entities, plus a dynamic set
discovered from the camera's IoT model values.

### Core entities

| Entity              | Platform        | Purpose |
|---------------------|-----------------|---------|
| Camera              | `camera`        | Live view (WebRTC via HA's built-in go2rtc, or HLS) + `stream_source` for Frigate. |
| Motion              | `binary_sensor` | PIR / motion / AI alert (person, pet, package, etc.). |
| Camera Awake        | `binary_sensor` | True while the camera is actively streaming. |
| Charging            | `binary_sensor` | USB charging state (battery cameras). |
| Battery             | `sensor`        | Charge percentage (battery cameras). |
| Charge Status       | `sensor`        | Discharging / Charging / Full text status. |
| Wake Camera         | `button`        | Manually wake the camera. |
| Wake on Motion      | `switch`        | Toggle auto-wake when a motion event fires. |
| Motion Timeout      | `number`        | How long the camera stays awake after motion (10–600 s). |
| Stream Host Mode    | `select`        | `Local Only` (default), `IP Address` or `Docker Hostname` — who can connect to the stream, see [Streaming notes](#streaming-notes). |
| Stream Quality      | `select`        | `AUTO`, `SD`, `HD`, `QHD`, … — advertised profiles, or the native HD/SD fallback for legacy cameras. |

### IoT entities (model-dependent)

The integration reads each camera's supported IoT feature codes on first
setup and exposes only those that exist. Expect a subset of:

- **Switches**: Status LED, Motion Detection, Person Detection, Human
  Tracking, Sound Detection, Crying Detection, ONVIF, SD Recording, PIR
  Sensor, Floodlight / Siren Linkage, Face Recognition, Sleep Mode, RGB
  Light, Anti-Jamming, PTZ Patrol, Laser Toy, Pet Alarm, HomeKit, Auto
  Update, OSD Watermark, Brand Logo, Sound, Lamp.
- **Numbers** (sliders): Motion / Sound / Human / PIR Sensitivity, PIR
  Interval, Floodlight Brightness & Duration, Speaker Volume, Warm Light
  Brightness.
- **Selects**: SD Record Type, Day/Night Mode, Alarm Frequency,
  Sound/Light Alarm Type, Anti-Flicker (50/60 Hz), Full Color Mode.
- **Sensors**: Temperature, Humidity, Baby Status (awake / crying /
  sleeping; polled every few minutes).
- **Baby monitors** (e.g. Arenti AINanny):
  - **Lullaby** `media_player` — play / pause / stop / next / previous,
    volume and repeat for the built-in lullabies and white noise. A track
    that ends by itself shows as playing until the next status poll.
  - **Night Light** `light` — on/off and colour. It supersedes the plain
    *RGB Light* switch, which is hidden on new installs.
  - **Crying** `binary_sensor` — on for a minute after each cry alert,
    delivered as fast as motion alerts.

Cameras that don't expose a feature simply won't show its entity.

### Services

| Service          | Description |
|------------------|-------------|
| `cloudplus.ptz`  | Move (`left`/`right`/`up`/`down`) or stop a PTZ camera. Target a `camera` entity. See [services.yaml](custom_components/cloudplus/services.yaml). |
| `cloudplus.ptz_preset` | `goto`, `save` (the current position) or `delete` preset slot `preset` (1–32) on a PTZ camera. |

### PTZ from a dashboard (press and hold)

Meari cameras pan/tilt on *start* and keep going until *stop*, exactly like
holding a direction in the official app. `cloudplus.ptz` mirrors that:
`action: move` on press, `action: stop` on release. Commands for one camera
are sent in order (a quick tap's stop never overtakes its move), and a move
that is never stopped (closed tab, lost connection) stops on its own after
20 seconds.

Home Assistant's built-in cards can't send anything on *release*, so for
hold-to-move use a card that can, e.g. the
[WebRTC Camera](https://github.com/AlexxIT/WebRTC) card: it calls
`data_start_<direction>` on press and `data_end_<direction>` on release.

```yaml
type: custom:webrtc-camera
entity: camera.cloudedge_meari_yard_camera
ptz:
  service: cloudplus.ptz
  data_start_left:  {entity_id: camera.cloudedge_meari_yard_camera, action: move, argument: left}
  data_end_left:    {entity_id: camera.cloudedge_meari_yard_camera, action: stop}
  data_start_right: {entity_id: camera.cloudedge_meari_yard_camera, action: move, argument: right}
  data_end_right:   {entity_id: camera.cloudedge_meari_yard_camera, action: stop}
  data_start_up:    {entity_id: camera.cloudedge_meari_yard_camera, action: move, argument: up}
  data_end_up:      {entity_id: camera.cloudedge_meari_yard_camera, action: stop}
  data_start_down:  {entity_id: camera.cloudedge_meari_yard_camera, action: move, argument: down}
  data_end_down:    {entity_id: camera.cloudedge_meari_yard_camera, action: stop}
```

With built-in cards (tile, button, picture-glance), use a **nudge** instead:
add `duration` and each tap moves for that long, then stops.

```yaml
tap_action:
  action: perform-action
  perform_action: cloudplus.ptz
  data: {entity_id: camera.cloudedge_meari_yard_camera, action: move, argument: left, duration: 0.5}
```

Each command is a round trip to the Meari cloud, so expect a fraction of a
second between pressing and the camera moving.

---

## Streaming notes

### What the camera entity exposes

The camera entity exposes its native stream as an HA-managed MPEG-TS source.

- **Video** is copied from the camera as-is. Depending on the chosen
  `Stream Quality`, the underlying codec is **H.264** (smaller, more robust
  — typically `SD`) or **HEVC / H.265** (higher quality, more fragile on
  battery cameras — typically `HD`, `QHD`, `AUTO`). Both codecs are
  delivered to consumers untouched, so quality is exactly what the camera
  produces.
- **Audio** (G.711 µ-law from the camera) is re-encoded to AAC so the
  output is a fully standard MPEG-TS.
- The result is reachable by Frigate, go2rtc, the native HA `stream`
  component, `media_player.play_media`, the HA mobile companion, ONVIF /
  RTSP clients, and most NVR-style add-ons.

### Idle stream (battery / snap cameras)

Battery cameras spend most of their life asleep — actually streaming only
while a PIR or motion event keeps them awake. Without help, consumers like
Frigate would see the source vanish every minute or two and fire a "lost
stream" event.

The integration solves this by **continuously publishing an idle stream**
while the camera is asleep:

- Same advertised codec, resolution and FPS as the live profile.
- Repeated still frame seeded from the most recent keyframe captured while
  the camera was awake (so it visually represents the actual scene).
- Low refresh cost — not re-encoded at high FPS.
- When the camera wakes up, the live frames take over **on the same TCP
  socket**, with no reconnect, no PAT/PMT renegotiation, and no client
  seeing the source go offline.

This is the difference between Frigate "just working" with these cameras
and Frigate being effectively unusable with them.

Some Meari doorbells are reported by the API as `doorbell` / `pictureDoorBell`
rather than `snap`, even though their capability map advertises `bat`. The
coordinator treats those battery-capable doorbell categories as battery
cameras so they use the same wake/idle-stream lifecycle.

### Live view in Home Assistant (built-in go2rtc / WebRTC)

Home Assistant 2024.11+ ships go2rtc and uses it for low-latency WebRTC
live view (it is part of `default_config`). The camera entity works with it
out of the box — no add-on, YAML or extra integration needed: HA hands the
camera's `http://` MPEG-TS source to its go2rtc, which serves the video
as-is and transcodes the AAC audio to Opus for the browser. Cards that use
HA's camera WebRTC, such as the WebRTC Camera card with `entity:`, go
through the same path.

- **H.264** (typically `SD`) plays over WebRTC in every browser.
- **HEVC / H.265** (typically `HD`, `QHD`, `AUTO`) only plays over WebRTC
  where the browser supports H.265 WebRTC (recent Safari / Chrome on
  supported hardware). Elsewhere HA's frontend falls back to HLS, which has
  the same HEVC limits plus a few seconds of latency. Pick `SD` under
  *Stream Quality* if you want WebRTC everywhere.
- Battery cameras keep their usual behaviour: opening the live view shows
  the idle still frame; press *Wake Camera* (or let motion wake it) to go
  live. Still images on dashboards come from the cached snapshot and never
  wake the camera.
- Mains-powered cameras stream on demand while someone watches. To keep
  the dashboard still current they also stream for ~30 s when a dashboard
  asks for an image and the cached one is missing or older than 5 minutes.

### Stream Host Mode

The `Stream Host Mode` select entity controls who can connect to the
MPEG-TS server and how its URL is advertised:

- `Local Only` (default) — listens on `127.0.0.1` only
  (`http://127.0.0.1:36059/`). HA's live view (built-in go2rtc / WebRTC and
  HLS) works; nothing else on your network can open the stream. The stream
  carries decrypted video and has no authentication, so keep this unless
  another machine or container needs it.
- `IP Address` — listens on all interfaces and advertises the HA host's LAN
  IP (e.g. `http://192.168.1.10:36059/`). Pick this for Frigate or a
  standalone go2rtc on another host or container that reaches HA by IP.
- `Docker Hostname` — listens on all interfaces and advertises the
  container's hostname instead. Pick this if Frigate / go2rtc runs in the
  **same Docker network** as HA and resolves containers by name.

If you pointed HA's own go2rtc at an external go2rtc server (`go2rtc: url:`
in `configuration.yaml`) that does not share HA's network namespace, pick
`IP Address` too. The choice is remembered across restarts, as are *Stream
Quality*, *Wake on Motion* and *Motion Timeout*.

### Using the stream in Frigate / a standalone go2rtc

First set *Stream Host Mode* to `IP Address` or `Docker Hostname`: the
default `Local Only` mode refuses connections from other hosts and
containers.

The MPEG-TS server is internal to Home Assistant — it isn't a hardcoded
RTSP URL you can paste into Frigate's config. You need to read the actual
URL out of the live HA `camera` entity and hand it to go2rtc / Frigate
each time the stream is opened.

The easiest setup is the
[`hass-expose-camera-stream-source`](https://github.com/felipecrs/hass-expose-camera-stream-source)
integration, which exposes the live `stream_source` of any HA camera
entity over a simple HTTP endpoint:

```
GET http://<ha-host>:8123/api/camera_stream_source/<camera_entity_id>
Authorization: Bearer <long-lived-access-token>
```

The response body is the current stream URL (e.g.
`http://192.168.1.10:36059/`; the same port also still answers raw `tcp://`
for older configs). go2rtc can call that endpoint on every stream
open via its `echo:` source type, which uses the stdout of an arbitrary
command as the URL:

1. **Install** `hass-expose-camera-stream-source` (HACS, integration). No
   YAML / configuration entry needed — it just enables the endpoint.
2. **Create a long-lived access token** in HA (profile menu → bottom of
   the page → *Long-Lived Access Tokens*).
3. **Point go2rtc at the endpoint**. Example for Frigate's bundled
   go2rtc, with a camera entity named `camera.cloudplus_camera`:

   ```yaml
   # frigate config.yml
   go2rtc:
     streams:
       cloudedge-camera:
         - 'echo:curl -fsSL http://<ha-host>:8123/api/camera_stream_source/camera.cloudplus_camera
             -H "Authorization: Bearer <ha-long-lived-access-token>"'

   cameras:
     cloudedge-camera:
       ffmpeg:
         inputs:
           - path: rtsp://127.0.0.1:8554/cloudedge-camera
             roles: [detect, record]
   ```

   Standalone go2rtc takes the exact same `echo:curl …` entry under its
   own `streams:` section.
4. Frigate then talks to go2rtc over RTSP, go2rtc re-resolves the HA
   endpoint each time a client connects, and you get a clean H.264 / HEVC
   feed that survives the camera sleeping (the idle stream covers the
   gap) and records / detects normally.

> 💡 If you'd rather not use the helper, you can read the `stream_source`
> attribute off the camera entity directly via the HA WebSocket or REST
> API and feed it to go2rtc however you prefer. The helper just packages
> that into a single token-protected GET.

### Deep technical details

For the underlying mechanics (KCP recovery, TURN/relay routing, HEVC
stalls, proactive `START_LIVE` re-issue, source-idle recovery), see
[docs/streaming.md](docs/streaming.md) and
[docs/protocol.md](docs/protocol.md).

---

## Troubleshooting

A standalone CLI harness (`debug.py`) reuses the exact code path the
integration takes, so you can repro problems outside Home Assistant:

```bash
# Copy .env.example or create your own .env with EMAIL/PASSWORD/COUNTRY_CODE
python debug.py list
python debug.py stream --device-id <id> --duration 60 --quality QHD
python debug.py --debug stream --device-id <id> --duration 30  # verbose logs
```

Common issues:

- **No motion events** — another device is probably holding the Meari MQTT
  session. Use a dedicated cloud account for HA (see [Setup](#setup)).
- **Stream stalls / "few frames" on QHD** — HEVC at high bitrate is fragile
  on battery cameras; dropping to SD (which is H.264) is a useful fallback.
  See [docs/diagnosis.md](docs/diagnosis.md).
- **Camera never wakes** — deep-dormancy wakes are retried automatically
  for up to ~45 s; if it consistently fails, your account may not own the
  wake right (re-share from the app).
- **Frigate can't connect / loses the source** — *Stream Host Mode*
  defaults to `Local Only`; set it to `IP Address` or `Docker Hostname`
  depending on your network topology.

---

## How it works (high level)

1. **Authenticate** with the Meari HTTP API (per-app-profile host family).
2. **Subscribe** to the Meari IoT MQTT broker (TLS) for motion / AI alerts.
3. **Discover** signaling servers via the native UDP root protocol.
4. **Signal** a WebRTC-like session over TCP MsgSvr (offer/answer, ICE).
5. **Relay** data through a TURN UDP allocation (or directly on the LAN).
6. **Tunnel** with KCP (reliable ARQ over UDP) and the IVA framing layer.
7. **Control** the camera with VVP commands (`START_LIVE`, heartbeat, etc.).
8. **Decrypt** the media — H.264 *or* HEVC video (3DES-ECB when the camera
   has E2EE enabled) and G.711 µ-law audio.
9. **Mux** to MPEG-TS — video is copied (whichever codec the camera sent),
   audio is encoded to AAC — and serve to all HA / Frigate / go2rtc
   consumers via a single fan-out TCP socket. The same socket carries the
   idle still-frame stream while the camera is asleep.

Each layer is documented in detail under [docs/](docs/).

---

## Documentation map

| File | Audience | What's inside |
|------|----------|---------------|
| [README.md](README.md) | Users | This file — install, setup, entities, services. |
| [AGENTS.md](AGENTS.md) | AI coding agents | Project entry points, commands, conventions, permissions. |
| [docs/architecture.md](docs/architecture.md) | Contributors | Code layout (custom_components, p2p_streamer, coordinator, debug_tools). |
| [docs/protocol.md](docs/protocol.md) | Protocol hackers | Discovery, signaling, ICE/TURN, KCP, VVP, media frames. |
| [docs/streaming.md](docs/streaming.md) | Contributors | Live-start patterns, source-idle recovery, MPEG-TS fan-out. |
| [docs/motion-events.md](docs/motion-events.md) | Contributors | Meari MQTT topics, payload shapes, fallback notification API. |
| [docs/diagnosis.md](docs/diagnosis.md) | Contributors | `debug.py` usage, log signals, how to triage stalls. |

---

## Contributing

PRs welcome. If you're an AI assistant working in this repo, start with
[AGENTS.md](AGENTS.md). If you're a human contributor, the same file is also
the fastest way to get oriented.

Before opening a PR, please:

- run `python debug.py list` and `python debug.py stream …` against your own
  camera to confirm the change doesn't break the streaming path,
- keep behaviour gated on actual evidence from local captures of the official
  app where possible,
- avoid hardcoded IPs, regions, or signaling endpoints — discovery is the
  source of truth.

---

## Releases

Normal pushes to `main` do not create releases. Maintainers create releases
manually from **Actions → Release Tags → Run workflow** and choose one of:

- `prerelease` — creates the next `vX.Y.Z-dev.N` release.
- `release` — creates the stable `vX.Y.Z` release.

The version in `custom_components/cloudplus/manifest.json` is the source of
truth. After publishing a stable release, bump that version before starting
the next prerelease cycle.

---

## License

[MIT](LICENSE) — see file for details. The product names used in this README
are trademarks of their respective owners; this project is not endorsed by any
of them.
