from runtime_guard import (runtime_enabled, feature_enabled, token_matches, require_feature, install_runtime_guard)
import json, os, re, subprocess
import mysql.connector
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="Luminax Network Manager")
MYSQL_HOST = os.getenv("MYSQL_HOST", "mysql")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "luminax")
MYSQL_USER = os.getenv("MYSQL_USER", "")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
LOCAL_SUBNET = os.getenv("LOCAL_SUBNET", "").strip()

class RoomRequest(BaseModel):
    room_key: str
    display_name: str
    x: float | None = None
    y: float | None = None
    z: float | None = None
    rotation_deg: float | None = None
    meta_json: dict | None = None

class GroupRequest(BaseModel):
    group_key: str
    display_name: str
    group_type: str = "custom"
    meta_json: dict | None = None

class AssignRequest(BaseModel):
    device_key: str
    room_key: str | None = None
    group_key: str | None = None

def db_conn():
    return mysql.connector.connect(host=MYSQL_HOST, port=MYSQL_PORT, user=MYSQL_USER, password=MYSQL_PASSWORD, database=MYSQL_DATABASE)

def sh(cmd: str):
    require_feature("LUMINAX_NETWORK_SCAN_ENABLED")
    proc = subprocess.run(["/bin/sh", "-lc", cmd], capture_output=True, text=True)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}

def normalize_device(hostname: str | None, ip: str | None, mac: str | None, source: str, vendor: str | None = None):
    hostname = (hostname or "").strip() or None
    ip = (ip or "").strip() or None
    mac = (mac or "").strip().lower() or None
    vendor = (vendor or "").strip() or None
    base = mac or ip or hostname or f"unknown-{source}"
    device_type = "unknown"
    guess = (hostname or vendor or "").lower()
    if any(k in guess for k in ["tv", "roku", "firetv", "chromecast"]):
        device_type = "tv"
    elif any(k in guess for k in ["light", "bulb", "hue"]):
        device_type = "light"
    elif any(k in guess for k in ["fridge", "refrigerator"]):
        device_type = "appliance"
    elif any(k in guess for k in ["router", "gateway", "mesh", "ap"]):
        device_type = "network"
    return {
        "device_key": base,
        "hostname": hostname,
        "ip_address": ip,
        "mac_address": mac,
        "vendor": vendor,
        "device_type": device_type,
        "source": source,
    }

def upsert_device(item: dict):
    conn = db_conn(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO device_inventory
        (device_key, hostname, ip_address, mac_address, vendor, device_type, source, capabilities_json, location_json, state_json, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE
          hostname=VALUES(hostname),
          ip_address=VALUES(ip_address),
          mac_address=VALUES(mac_address),
          vendor=VALUES(vendor),
          device_type=VALUES(device_type),
          source=VALUES(source)
        """,
        (
            item["device_key"], item.get("hostname"), item.get("ip_address"), item.get("mac_address"), item.get("vendor"),
            item.get("device_type", "unknown"), item.get("source", "scan"), json.dumps(item.get("capabilities", {})),
            json.dumps(item.get("location", {})), json.dumps(item.get("state", {})), json.dumps(item.get("meta", {}))
        )
    )
    conn.commit(); cur.close(); conn.close()

def scan_ip_neigh():
    results = []
    raw = sh("ip neigh || true")["stdout"]
    for line in raw.splitlines():
        m = re.match(r"^(\S+)\s+dev\s+(\S+)\s+lladdr\s+(\S+)\s+(\S+)", line.strip())
        if not m:
            continue
        ip, iface, mac, state = m.groups()
        item = normalize_device(None, ip, mac, "ip-neigh")
        item["meta"] = {"iface": iface, "neighbor_state": state}
        results.append(item)
    return results

def scan_arp_table():
    results = []
    raw = sh("arp -an || true")["stdout"]
    for line in raw.splitlines():
        m = re.search(r"\(([^)]+)\) at ([0-9a-fA-F:]+)", line)
        if not m:
            continue
        ip, mac = m.groups()
        results.append(normalize_device(None, ip, mac, "arp"))
    return results

@app.get("/health")
async def health():
    return {"status": "ok", "mode": "network_awareness_foundation", "subnet": LOCAL_SUBNET}

@app.post("/devices/scan")
async def devices_scan():
    found = []
    for item in scan_ip_neigh() + scan_arp_table():
        found.append(item)
        upsert_device(item)
    return {"status": "ok", "count": len(found), "devices": found[:50]}

@app.get("/devices")
async def devices_list():
    try:
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM device_inventory ORDER BY updated_at DESC, id DESC LIMIT 200")
        rows = cur.fetchall(); cur.close(); conn.close()
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}

@app.get("/rooms")
async def rooms_list():
    try:
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM house_rooms ORDER BY id DESC LIMIT 200")
        rows = cur.fetchall(); cur.close(); conn.close()
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}

@app.get("/groups")
async def groups_list():
    try:
        conn = db_conn(); cur = conn.cursor(dictionary=True)
        cur.execute("SELECT * FROM device_groups ORDER BY id DESC LIMIT 200")
        rows = cur.fetchall(); cur.close(); conn.close()
        return {"rows": rows}
    except Exception as e:
        return {"rows": [], "error": str(e)}

@app.post("/rooms/upsert")
async def rooms_upsert(req: RoomRequest):
    conn = db_conn(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO house_rooms (room_key, display_name, x, y, z, rotation_deg, meta_json)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), x=VALUES(x), y=VALUES(y), z=VALUES(z), rotation_deg=VALUES(rotation_deg), meta_json=VALUES(meta_json)
        """,
        (req.room_key, req.display_name, req.x, req.y, req.z, req.rotation_deg, json.dumps(req.meta_json or {}))
    )
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "room_key": req.room_key}

@app.post("/groups/upsert")
async def groups_upsert(req: GroupRequest):
    conn = db_conn(); cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO device_groups (group_key, display_name, group_type, meta_json)
        VALUES (%s,%s,%s,%s)
        ON DUPLICATE KEY UPDATE display_name=VALUES(display_name), group_type=VALUES(group_type), meta_json=VALUES(meta_json)
        """,
        (req.group_key, req.display_name, req.group_type, json.dumps(req.meta_json or {}))
    )
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "group_key": req.group_key}

@app.post("/assign")
async def assign(req: AssignRequest):
    conn = db_conn(); cur = conn.cursor()
    if req.room_key is not None:
        cur.execute("UPDATE device_inventory SET room_key=%s WHERE device_key=%s", (req.room_key, req.device_key))
    if req.group_key is not None:
        cur.execute(
            "INSERT INTO device_group_members (group_key, device_key, meta_json) VALUES (%s,%s,%s) ON DUPLICATE KEY UPDATE meta_json=VALUES(meta_json)",
            (req.group_key, req.device_key, json.dumps({}))
        )
    conn.commit(); cur.close(); conn.close()
    return {"status": "ok", "device_key": req.device_key, "room_key": req.room_key, "group_key": req.group_key}



install_runtime_guard(app, 'network-manager', require_auth=True, required_feature='LUMINAX_NETWORK_SCAN_ENABLED')
