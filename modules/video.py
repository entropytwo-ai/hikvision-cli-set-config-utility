"""Video encoding settings (ISAPI /ISAPI/Streaming/channels/<id>).

Stream channel ids are ``<channel><stream>``: 101 = channel 1 main stream,
102 = channel 1 sub stream, 103 = channel 1 third stream.
Hikvision stores the frame rate multiplied by 100 (25 fps -> 2500).
"""
from __future__ import annotations

from typing import Any, Dict, List

from .camera_client import CameraClient, find, set_text, text
from .common import add_command, channel_of, int_range, on_off, resolution

STREAM_PATH = "/ISAPI/Streaming/channels/{id}"
STREAMS = {"main": 1, "sub": 2, "third": 3}
CODECS = {"h264": "H.264", "h.264": "H.264", "h265": "H.265", "h.265": "H.265", "mjpeg": "MJPEG"}


def stream_id(channel: int, stream: str) -> int:
    return channel * 100 + STREAMS[stream]


def _fps(raw: str) -> str:
    try:
        value = int(raw) / 100
        return f"{value:g}"
    except (TypeError, ValueError):
        return raw


class Video:
    def __init__(self, client: CameraClient, channel: int = 1):
        self.client = client
        self.channel = channel

    def get(self, stream: str) -> Dict[str, Any]:
        root = self.client.get_xml(STREAM_PATH.format(id=stream_id(self.channel, stream)))
        v = find(root, "Video")
        control = text(v, "videoQualityControlType")
        w, h = text(v, "videoResolutionWidth"), text(v, "videoResolutionHeight")
        result = {
            "stream": f"{stream} ({stream_id(self.channel, stream)})",
            "enabled": text(root, "enabled"),
            "codec": text(v, "videoCodecType"),
            "resolution": f"{w}x{h}" if w and h else None,
            "framerate_fps": _fps(text(v, "maxFrameRate")),
            "bitrate_control": control,
            "bitrate_kbps": text(v, "constantBitRate") if (control or "").upper() == "CBR"
            else text(v, "vbrUpperCap"),
            "vbr_quality": text(v, "fixedQuality"),
            "gop_length": text(v, "GovLength"),
            "profile": text(v, "H264Profile") or text(v, "H265Profile"),
            "smart_codec": text(v, "SmartCodec/enabled"),
        }
        return {k: v for k, v in result.items() if v is not None}

    def set(self, stream: str, codec=None, res=None, fps=None, bitrate_mode=None, bitrate=None,
            quality=None, gop=None, profile=None, enabled=None) -> List[str]:
        path = STREAM_PATH.format(id=stream_id(self.channel, stream))
        root = self.client.get_xml(path)
        video = find(root, "Video")
        if video is None:
            raise ValueError("stream has no <Video> section")
        changes: List[str] = []
        if enabled is not None:
            set_text(root, "enabled", enabled)
            changes.append(f"stream {'enabled' if enabled else 'disabled'}")
        if codec:
            set_text(video, "videoCodecType", CODECS[codec.lower()])
            changes.append(f"codec {CODECS[codec.lower()]}")
        if res:
            set_text(video, "videoResolutionWidth", res[0])
            set_text(video, "videoResolutionHeight", res[1])
            changes.append(f"resolution {res[0]}x{res[1]}")
        if fps:
            set_text(video, "maxFrameRate", int(round(fps * 100)))
            changes.append(f"framerate {fps:g} fps")
        mode = (bitrate_mode or text(video, "videoQualityControlType") or "CBR").upper()
        if bitrate_mode:
            set_text(video, "videoQualityControlType", mode)
            changes.append(f"bitrate control {mode}")
        if bitrate:
            set_text(video, "constantBitRate" if mode == "CBR" else "vbrUpperCap", bitrate)
            changes.append(f"bitrate {bitrate} kbps ({mode})")
        if quality is not None:
            set_text(video, "fixedQuality", quality)
            changes.append(f"VBR quality {quality}")
        if gop:
            set_text(video, "GovLength", gop)
            changes.append(f"GOP {gop}")
        if profile:
            tag = "H265Profile" if (text(video, "videoCodecType") or "").upper() == "H.265" else "H264Profile"
            set_text(video, tag, profile)
            changes.append(f"{tag} {profile}")
        self.client.put_xml(path, root)
        return changes


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def cmd_get_video(client, log, args, camera) -> None:
    video = Video(client, channel_of(args, camera))
    for stream in (args.stream or ["main", "sub"]):
        with log.step(f"retrieve {stream} stream video config"):
            info = video.get(stream)
            log.ok(f"Video config ({stream} stream) retrieved successfully")
            log.data(f"Video - {stream} stream", info, f"video_{stream}")


def cmd_set_video(client, log, args, camera) -> None:
    video = Video(client, channel_of(args, camera))
    for stream in (args.stream or ["main"]):
        with log.step(f"set {stream} stream video config"):
            changes = video.set(stream, args.codec, args.resolution, args.fps, args.bitrate_mode,
                                args.bitrate, args.quality, args.gop, args.profile, args.enabled)
            log.ok(f"Video ({stream} stream) set to: " + ", ".join(changes))


def register(sub, parents) -> None:
    p = add_command(sub, "get-video", "Read codec, resolution, framerate and bitrate per stream",
                    cmd_get_video, parents)
    p.add_argument("--stream", choices=list(STREAMS), action="append",
                   help="stream to read (repeatable; default: main and sub)")
    p.add_argument("--channel", type=int, help="video channel (default 1 or camera file value)")

    p = add_command(sub, "set-video", "Set codec, resolution, framerate, bitrate and GOP",
                    cmd_set_video, parents, writes=True,
                    change_opts=["codec", "resolution", "fps", "bitrate_mode", "bitrate", "quality",
                                 "gop", "profile", "enabled"],
                    epilog=("The camera validates values against its capabilities; unsupported\n"
                            "combinations (e.g. a resolution the sub stream cannot do) are rejected\n"
                            "and reported as failures for that camera."))
    p.add_argument("--stream", choices=list(STREAMS), action="append",
                   help="stream to change (repeatable; default: main)")
    p.add_argument("--channel", type=int, help="video channel (default 1 or camera file value)")
    p.add_argument("--codec", type=str.lower, choices=["h264", "h265", "mjpeg"], help="video codec")
    p.add_argument("--resolution", type=resolution, metavar="WxH", help="e.g. 1920x1080")
    p.add_argument("--fps", type=float, help="max frame rate (e.g. 25, 30, 12.5)")
    p.add_argument("--bitrate-mode", type=str.upper, choices=["CBR", "VBR"], help="bitrate control")
    p.add_argument("--bitrate", type=int_range(32, 32768), metavar="KBPS",
                   help="bitrate in kbps (CBR: constant; VBR: upper cap)")
    p.add_argument("--quality", type=int_range(1, 100),
                   help="VBR image quality 1-100 (Hikvision: 1=lowest … 100=highest)")
    p.add_argument("--gop", type=int_range(1, 400), help="I-frame interval (GOP length)")
    p.add_argument("--profile", choices=["baseline", "main", "high"], help="H.264/H.265 profile")
    p.add_argument("--enabled", type=on_off, metavar="on|off", help="enable/disable the stream (sub/third)")
