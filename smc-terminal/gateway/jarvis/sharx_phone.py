"""SHARX INVEST plugin for Mark LVII (FatihMakes): lets JARVIS on this PC hand a request to JARVIS on the user's
Android phone (SHARX app) and hear what the phone answered.

SHARX desktop puts this file in Mark LVII's plugins/ folder and writes config/sharx_phone.json when the phone is paired
(JARVIS tab → KONTROL HP). The phone accepts requests only from a paired PC, only while "Izinkan PC mengontrol HP" is on
in its JARVIS tab, and only over the local network.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

PLUGIN = {
    "name": "phone_command",
    "description": (
        "Send a request to JARVIS on the user's Android phone (the SHARX app) and return what the phone answered. "
        "Use it only for things that must happen on the phone: its apps, reminders on the phone, phone volume or "
        "flashlight, preparing a call or message on the phone, or the SHARX market app on the phone."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "command": {"type": "STRING", "description": "The request for the phone, in the user's own words"},
        },
        "required": ["command"],
    },
}

CONFIG = Path(__file__).resolve().parent.parent / "config" / "sharx_phone.json"


def run(parameters: dict, player=None, session_memory=None) -> str:
    command = " ".join(str((parameters or {}).get("command") or "").split())[:2000]
    if not command:
        return "Tell me what the phone should do."
    try:
        config = json.loads(CONFIG.read_text(encoding="utf-8"))
        address, token = str(config["address"]), str(config["token"])
    except (OSError, ValueError, KeyError):
        return "The phone is not paired yet. Pair it in SHARX on this PC: JARVIS tab, KONTROL HP."
    request = urllib.request.Request(
        f"http://{address}/command",
        data=json.dumps({"text": command}).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local network: never through a proxy
    try:
        with opener.open(request, timeout=50) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return "The phone no longer accepts this PC. Pair it again in SHARX: JARVIS tab, KONTROL HP."
        return f"The phone answered with HTTP {e.code}."
    except (urllib.error.URLError, OSError, ValueError) as e:
        return (f"The phone did not answer ({type(e).__name__}). SHARX must be open on the phone, with 'Izinkan PC "
                f"mengontrol HP' on, on the same Wi-Fi.")
    reply = " ".join(str(data.get("reply") or "").split())
    if player:
        try:
            player.write_log(f"SYS: phone → {reply or data.get('detail') or 'sent'}")
        except Exception:
            pass
    return f"The phone's JARVIS answered: {reply}" if reply else str(data.get("detail") or "Sent to the phone.")
