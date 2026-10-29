"""A tiny in-memory fake Hikvision ISAPI server for offline testing.

    python3 tests/mock_camera.py --port 8081 [--auth digest|basic] [--no-ptz]

Credentials: admin / Passw0rd!  -  GET returns the stored XML, PUT replaces it,
POST/DELETE on /ISAPI/Security/users add/remove users.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import threading
import time
from base64 import b64decode
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

NS = 'xmlns="http://www.hikvision.com/ver20/XMLSchema" version="2.0"'
USER, PASSWORD, REALM = "admin", "Passw0rd!", "DS-2CD2143G2"


def seed() -> dict:
    return {
        "/ISAPI/System/deviceInfo": f"""<DeviceInfo {NS}><deviceName>IP CAMERA</deviceName>
<deviceID>abc</deviceID><model>DS-2CD2143G2-I</model><serialNumber>DS-2CD2143G2-I20230101AAWRJ00000</serialNumber>
<macAddress>bc:ba:c2:00:00:01</macAddress><firmwareVersion>V5.7.3</firmwareVersion>
<firmwareReleasedDate>build 220112</firmwareReleasedDate><deviceType>IPCamera</deviceType></DeviceInfo>""",
        "/ISAPI/System/status": f"""<DeviceStatus {NS}><currentDeviceTime>2026-10-29T12:00:00-05:00</currentDeviceTime>
<deviceUpTime>200000</deviceUpTime><CPUList><CPU><cpuDescription>ARM</cpuDescription><cpuUtilization>12</cpuUtilization></CPU></CPUList>
<MemoryList><Memory><memoryDescription>DDR</memoryDescription><memoryUsage>120.0</memoryUsage><memoryAvailable>80.0</memoryAvailable></Memory></MemoryList></DeviceStatus>""",
        "/ISAPI/System/time": f"""<Time {NS}><timeMode>manual</timeMode><localTime>2026-10-29T12:00:00-05:00</localTime>
<timeZone>EST5:00:00EDT,M3.2.0/02:00:00,M11.1.0/02:00:00</timeZone></Time>""",
        "/ISAPI/System/time/ntpServers/1": f"""<NTPServer {NS}><id>1</id><addressingFormatType>hostname</addressingFormatType>
<hostName>time.windows.com</hostName><portNo>123</portNo><synchronizeInterval>1440</synchronizeInterval></NTPServer>""",
        "/ISAPI/System/Network/interfaces/1": f"""<NetworkInterface {NS}><id>1</id><Link><MACAddress>bc:ba:c2:00:00:01</MACAddress>
<autoNegotiation>true</autoNegotiation><MTU>1500</MTU></Link></NetworkInterface>""",
        "/ISAPI/System/Network/interfaces/1/ipAddress": f"""<IPAddress {NS}><ipVersion>dual</ipVersion>
<addressingType>static</addressingType><ipAddress>192.168.1.64</ipAddress><subnetMask>255.255.255.0</subnetMask>
<DefaultGateway><ipAddress>192.168.1.1</ipAddress></DefaultGateway><PrimaryDNS><ipAddress>8.8.8.8</ipAddress></PrimaryDNS>
<SecondaryDNS><ipAddress>8.8.4.4</ipAddress></SecondaryDNS></IPAddress>""",
        "/ISAPI/Streaming/channels/101": f"""<StreamingChannel {NS}><id>101</id><channelName>Camera 01</channelName><enabled>true</enabled>
<Video><enabled>true</enabled><videoInputChannelID>1</videoInputChannelID><videoCodecType>H.264</videoCodecType>
<videoScanType>progressive</videoScanType><videoResolutionWidth>2560</videoResolutionWidth><videoResolutionHeight>1440</videoResolutionHeight>
<videoQualityControlType>VBR</videoQualityControlType><constantBitRate>4096</constantBitRate><fixedQuality>60</fixedQuality>
<vbrUpperCap>4096</vbrUpperCap><maxFrameRate>2500</maxFrameRate><GovLength>50</GovLength><H264Profile>Main</H264Profile>
<SmartCodec><enabled>false</enabled></SmartCodec></Video></StreamingChannel>""",
        "/ISAPI/Streaming/channels/102": f"""<StreamingChannel {NS}><id>102</id><channelName>Camera 01</channelName><enabled>true</enabled>
<Video><videoCodecType>H.264</videoCodecType><videoResolutionWidth>640</videoResolutionWidth><videoResolutionHeight>480</videoResolutionHeight>
<videoQualityControlType>CBR</videoQualityControlType><constantBitRate>512</constantBitRate><maxFrameRate>1500</maxFrameRate>
<GovLength>30</GovLength></Video></StreamingChannel>""",
        "/ISAPI/ContentMgmt/record/tracks/101": f"""<Track {NS}><id>101</id><Channel>101</Channel><Enable>true</Enable>
<Description>trackType=standard</Description><LoopEnable>true</LoopEnable><Duration>P10DT0H</Duration>
<TrackSchedule><ScheduleBlock><ScheduleBlockGUID>{{0}}</ScheduleBlockGUID><ScheduleBlockType>www.std-cgi.com/racm/schedule/ver10</ScheduleBlockType>
<ScheduleAction><id>1</id><ScheduleActionStartTime><DayOfWeek>Monday</DayOfWeek><TimeOfDay>00:00:00</TimeOfDay></ScheduleActionStartTime>
<ScheduleActionEndTime><DayOfWeek>Monday</DayOfWeek><TimeOfDay>24:00:00</TimeOfDay></ScheduleActionEndTime><ScheduleDSTEnable>false</ScheduleDSTEnable>
<Description>nothing</Description><Actions><Record>true</Record><Log>false</Log><SaveImg>false</SaveImg><ActionRecordingMode>CMR</ActionRecordingMode></Actions></ScheduleAction>
</ScheduleBlock></TrackSchedule><CustomExtensionList><CustomExtension><CustomExtensionName>www.hikvision.com/RaCM/trackCustomExt/timeSpan</CustomExtensionName>
<enableSchedule>true</enableSchedule><SaveAudio>false</SaveAudio><PreRecordTimeSeconds>5</PreRecordTimeSeconds><PostRecordTimeSeconds>5</PostRecordTimeSeconds>
</CustomExtension></CustomExtensionList></Track>""",
        "/ISAPI/System/Video/inputs/channels/1": f"""<VideoInputChannel {NS}><id>1</id><inputPort>1</inputPort><name>Camera 01</name></VideoInputChannel>""",
        "/ISAPI/System/Video/inputs/channels/1/overlays": f"""<VideoOverlay {NS}><normalizedScreenSize><normalizedScreenWidth>704</normalizedScreenWidth>
<normalizedScreenHeight>576</normalizedScreenHeight></normalizedScreenSize><attribute><transparent>false</transparent></attribute>
<TextOverlayList size="4"><TextOverlay><id>1</id><enabled>false</enabled><positionX>16</positionX><positionY>64</positionY><displayText></displayText></TextOverlay></TextOverlayList>
<DateTimeOverlay><enabled>true</enabled><positionX>0</positionX><positionY>544</positionY><dateStyle>MM-DD-YYYY</dateStyle>
<timeStyle>24hour</timeStyle><displayWeek>true</displayWeek></DateTimeOverlay>
<channelNameOverlay><enabled>true</enabled><positionX>512</positionX><positionY>64</positionY></channelNameOverlay></VideoOverlay>""",
        "/ISAPI/System/Video/inputs/channels/1/overlays/text/1": f"""<TextOverlay {NS}><id>1</id><enabled>false</enabled><positionX>16</positionX>
<positionY>64</positionY><displayText></displayText></TextOverlay>""",
        "/ISAPI/System/Video/inputs/channels/1/motionDetection": f"""<MotionDetection {NS}><enabled>false</enabled><enableHighlight>true</enableHighlight>
<samplingInterval>2</samplingInterval><startTriggerTime>500</startTriggerTime><endTriggerTime>500</endTriggerTime><regionType>grid</regionType>
<Grid><rowGranularity>18</rowGranularity><columnGranularity>22</columnGranularity></Grid>
<MotionDetectionLayout version="2.0"><sensitivityLevel>60</sensitivityLevel><layout><gridMap>{'000000' * 18}</gridMap></layout></MotionDetectionLayout></MotionDetection>""",
        "/ISAPI/Event/triggers/VMD-1": f"""<EventTrigger {NS}><id>VMD-1</id><eventType>VMD</eventType><videoInputChannelID>1</videoInputChannelID>
<EventTriggerNotificationList><EventTriggerNotification><id>center</id><notificationMethod>center</notificationMethod>
<notificationRecurrence>beginning</notificationRecurrence></EventTriggerNotification></EventTriggerNotificationList></EventTrigger>""",
        "/ISAPI/System/Network/mailing": f"""<mailingList {NS}><mailing><id>1</id><sender><name>camera</name><emailAddress>cam@example.com</emailAddress>
<smtp><enableAuthorization>false</enableAuthorization><enableSSL>false</enableSSL><addressingFormatType>hostname</addressingFormatType>
<hostName>smtp.example.com</hostName><portNo>25</portNo><accountName></accountName></smtp></sender>
<receiverList><receiver><id>1</id><name>ops</name><emailAddress>ops@example.com</emailAddress></receiver></receiverList>
<attachment><snapshot><enabled>false</enabled></snapshot></attachment></mailing></mailingList>""",
        "/ISAPI/Security/users": f"""<UserList {NS}><User><id>1</id><userName>admin</userName><userLevel>Administrator</userLevel></User>
<User><id>2</id><userName>viewer1</userName><userLevel>Viewer</userLevel></User></UserList>""",
        "/ISAPI/PTZCtrl/channels/1/presets": f"""<PTZPresetList {NS}><PTZPreset><id>1</id><presetName>Gate</presetName><enabled>true</enabled></PTZPreset>
<PTZPreset><id>2</id><presetName>Parking</presetName><enabled>true</enabled></PTZPreset></PTZPresetList>""",
    }


ERROR = f"""<ResponseStatus {NS}><requestURL>{{url}}</requestURL><statusCode>4</statusCode>
<statusString>{{status}}</statusString><subStatusCode>{{sub}}</subStatusCode></ResponseStatus>"""
OK = f"""<ResponseStatus {NS}><requestURL>{{url}}</requestURL><statusCode>1</statusCode><statusString>OK</statusString>
<subStatusCode>ok</subStatusCode></ResponseStatus>"""


class Handler(BaseHTTPRequestHandler):
    store: dict = {}
    auth_mode = "digest"
    ptz = True
    lock = threading.Lock()
    nonce = hashlib.md5(str(time.time()).encode()).hexdigest()

    def log_message(self, *a):  # silence
        pass

    def _send(self, code: int, body: str = "", headers=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        if self.auth_mode == "basic" and header.startswith("Basic "):
            return b64decode(header[6:]).decode() == f"{USER}:{PASSWORD}"
        if self.auth_mode == "digest" and header.startswith("Digest "):
            f = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
            ha1 = hashlib.md5(f"{f.get('username')}:{REALM}:{PASSWORD}".encode()).hexdigest()
            ha2 = hashlib.md5(f"{self.command}:{f.get('uri')}".encode()).hexdigest()
            expect = hashlib.md5(f"{ha1}:{f.get('nonce')}:{f.get('nc')}:{f.get('cnonce')}:auth:{ha2}"
                                 .encode()).hexdigest()
            return f.get("username") == USER and f.get("response") == expect
        return False

    def _challenge(self):
        if self.auth_mode == "basic":
            hdr = f'Basic realm="{REALM}"'
        else:
            hdr = f'Digest qop="auth", realm="{REALM}", nonce="{self.nonce}", stale="FALSE"'
        self._send(401, ERROR.format(url=self.path, status="Unauthorized", sub="unauthorized"),
                   {"WWW-Authenticate": hdr})

    def _handle(self):
        if not self._authorised():
            return self._challenge()
        path = self.path.split("?")[0]
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length).decode() if length else ""
        if path.startswith("/ISAPI/PTZCtrl") and not self.ptz:
            return self._send(403, ERROR.format(url=path, status="Invalid Operation", sub="notSupport"))
        with self.lock:
            if self.command == "GET":
                if path == "/ISAPI/System/configurationData":
                    return self._send(200, "BINARYCONFIG")
                if path in self.store:
                    return self._send(200, self.store[path])
                return self._send(404, ERROR.format(url=path, status="Invalid XML Content", sub="notSupport"))
            if self.command == "PUT":
                if path in ("/ISAPI/System/reboot", "/ISAPI/System/factoryReset") or path.endswith("/goto"):
                    return self._send(200, OK.format(url=path))
                if "<" not in body:
                    return self._send(400, ERROR.format(url=path, status="Invalid XML Format", sub="badXmlFormat"))
                if path.startswith("/ISAPI/Security/users/"):
                    return self._send(200, OK.format(url=path))
                if path.startswith("/ISAPI/PTZCtrl/channels/1/presets/"):
                    return self._send(200, OK.format(url=path))
                if path == "/ISAPI/Streaming/channels/101" and "H.266" in body:
                    return self._send(400, ERROR.format(url=path, status="Invalid Content", sub="badParameters"))
                self.store[path] = body
                return self._send(200, OK.format(url=path))
            if self.command == "POST" and path == "/ISAPI/Security/users":
                user = re.search(r"<User.*</User>", body, re.S).group(0)
                user = re.sub(r"<password>.*?</password>", "", user)
                self.store[path] = self.store[path].replace("</UserList>", user + "</UserList>")
                return self._send(200, OK.format(url=path))
            if self.command == "DELETE" and path.startswith("/ISAPI/Security/users/"):
                uid = path.rsplit("/", 1)[1]
                self.store["/ISAPI/Security/users"] = re.sub(
                    rf"<User[^>]*>\s*<id>{uid}</id>.*?</User>", "", self.store["/ISAPI/Security/users"], flags=re.S)
                return self._send(200, OK.format(url=path))
        return self._send(405, ERROR.format(url=path, status="Invalid Operation", sub="methodNotAllowed"))

    do_GET = do_PUT = do_POST = do_DELETE = _handle


def serve(port: int, auth: str = "digest", ptz: bool = True) -> ThreadingHTTPServer:
    handler = type("CamHandler", (Handler,), {"store": seed(), "auth_mode": auth, "ptz": ptz})
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    ap.add_argument("--auth", choices=["digest", "basic"], default="digest")
    ap.add_argument("--no-ptz", action="store_true")
    a = ap.parse_args()
    srv = serve(a.port, a.auth, not a.no_ptz)
    print(f"Mock Hikvision camera on 127.0.0.1:{a.port} ({a.auth}) - Ctrl+C to stop (pid {os.getpid()})")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        srv.shutdown()
