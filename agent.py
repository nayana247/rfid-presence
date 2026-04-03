import serial
import threading
import time
import requests
from datetime import datetime

# ── Config ────────────────────────────────────────────────
COM_PORT = 'COM9'
BAUD_RATE = 9600
TIMEOUT_SECONDS = 3
SERVER_URL = 'https://your-app.onrender.com'  # ← Replace with your Render URL after deployment

# ── Session State ─────────────────────────────────────────
current_session = {
    'uid': None,
    'card_name': None,
    'connected_at': None,
    'last_seen': None,
    'active': False
}
session_lock = threading.Lock()

# ── Helpers ───────────────────────────────────────────────
def get_card_name(uid):
    try:
        res = requests.post(f'{SERVER_URL}/agent/card-name', json={'uid': uid}, timeout=5)
        return res.json().get('name', 'Unknown Card')
    except:
        return 'Unknown Card'

def send_event(payload):
    try:
        requests.post(f'{SERVER_URL}/agent/card-event', json=payload, timeout=5)
    except Exception as e:
        print(f"❌ Failed to send event: {e}")

def send_arduino_status(connected, com_port):
    try:
        requests.post(f'{SERVER_URL}/agent/arduino-status', json={
            'connected': connected,
            'com_port': com_port,
            'last_signal': datetime.now().isoformat() if connected else None
        }, timeout=5)
    except Exception as e:
        print(f"❌ Failed to send arduino status: {e}")

# ── Timeout Watcher ───────────────────────────────────────
def timeout_watcher():
    while True:
        time.sleep(0.5)
        with session_lock:
            if current_session['active'] and current_session['last_seen']:
                elapsed = (datetime.now() - current_session['last_seen']).total_seconds()
                if elapsed >= TIMEOUT_SECONDS:
                    disconnected_at = datetime.now()
                    duration = int((disconnected_at - current_session['connected_at']).total_seconds())
                    payload = {
                        'event': 'disconnected',
                        'uid': current_session['uid'],
                        'card_name': current_session['card_name'],
                        'connected_at': current_session['connected_at'].strftime('%Y-%m-%d %H:%M:%S'),
                        'disconnected_at': disconnected_at.strftime('%Y-%m-%d %H:%M:%S'),
                        'duration_seconds': duration
                    }
                    print(f"[{disconnected_at.strftime('%H:%M:%S')}] {current_session['card_name']} DISCONNECTED — {duration}s")
                    send_event(payload)
                    current_session['uid'] = None
                    current_session['card_name'] = None
                    current_session['connected_at'] = None
                    current_session['last_seen'] = None
                    current_session['active'] = False

# ── Process UID ───────────────────────────────────────────
def process_uid(uid):
    with session_lock:
        now = datetime.now()
        if not current_session['active']:
            card_name = get_card_name(uid)
            current_session['uid'] = uid
            current_session['card_name'] = card_name
            current_session['connected_at'] = now
            current_session['last_seen'] = now
            current_session['active'] = True
            payload = {
                'event': 'connected',
                'uid': uid,
                'card_name': card_name,
                'connected_at': now.strftime('%Y-%m-%d %H:%M:%S')
            }
            print(f"[{now.strftime('%H:%M:%S')}] {card_name} CONNECTED")
            send_event(payload)

        elif current_session['uid'] == uid:
            current_session['last_seen'] = now

        else:
            # Different card — end current session
            disconnected_at = now
            duration = int((disconnected_at - current_session['connected_at']).total_seconds())
            payload = {
                'event': 'disconnected',
                'uid': current_session['uid'],
                'card_name': current_session['card_name'],
                'connected_at': current_session['connected_at'].strftime('%Y-%m-%d %H:%M:%S'),
                'disconnected_at': disconnected_at.strftime('%Y-%m-%d %H:%M:%S'),
                'duration_seconds': duration
            }
            send_event(payload)

            # Start new session
            card_name = get_card_name(uid)
            current_session['uid'] = uid
            current_session['card_name'] = card_name
            current_session['connected_at'] = now
            current_session['last_seen'] = now
            current_session['active'] = True
            send_event({
                'event': 'connected',
                'uid': uid,
                'card_name': card_name,
                'connected_at': now.strftime('%Y-%m-%d %H:%M:%S')
            })

# ── Serial Reader ─────────────────────────────────────────
def read_serial():
    while True:
        try:
            ser = serial.Serial(COM_PORT, BAUD_RATE, timeout=1)
            print(f"✅ Connected to {COM_PORT}")
            send_arduino_status(True, COM_PORT)
            while True:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line.startswith("Card UID:"):
                    uid = line.replace("Card UID:", "").strip()
                    send_arduino_status(True, COM_PORT)
                    process_uid(uid)
        except Exception as e:
            print(f"❌ Serial error: {e}. Retrying in 3s...")
            send_arduino_status(False, COM_PORT)
            time.sleep(3)

# ── Main ──────────────────────────────────────────────────
if __name__ == '__main__':
    print(f"🚀 RFID Agent starting — connecting to {SERVER_URL}")
    threading.Thread(target=timeout_watcher, daemon=True).start()
    read_serial()
