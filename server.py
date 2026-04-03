from flask import Flask, render_template, jsonify, request, Response
from flask_socketio import SocketIO
import firebase_admin
from firebase_admin import credentials, db
import os
import json
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'rfid_secret_key'
socketio = SocketIO(app, cors_allowed_origins="*")

# ── Firebase Init ─────────────────────────────────────────
firebase_key = os.environ.get('FIREBASE_KEY')
if firebase_key:
    key_dict = json.loads(firebase_key)
    cred = credentials.Certificate(key_dict)
else:
    cred = credentials.Certificate('serviceAccountKey.json')

firebase_admin.initialize_app(cred, {
    'databaseURL': 'https://rfid-presence-tap-and-hold-default-rtdb.asia-southeast1.firebasedatabase.app'
})

# ── Firebase References ───────────────────────────────────
def get_cards_ref():
    return db.reference('/cards')

def get_sessions_ref():
    return db.reference('/sessions')

def get_current_ref():
    return db.reference('/current')

def get_arduino_ref():
    return db.reference('/arduino')

# ── Routes ────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/current')
def get_current():
    data = get_current_ref().get()
    if data and data.get('active'):
        connected_at = datetime.strptime(data['connected_at'], '%Y-%m-%d %H:%M:%S')
        elapsed = int((datetime.now() - connected_at).total_seconds())
        data['elapsed_seconds'] = elapsed
        return jsonify(data)
    return jsonify({'active': False})

@app.route('/api/sessions')
def get_sessions():
    data = get_sessions_ref().get()
    if not data:
        return jsonify([])
    sessions = list(data.values())
    sessions.sort(key=lambda x: x.get('id', 0), reverse=True)
    return jsonify(sessions[:100])

@app.route('/api/stats')
def get_stats():
    data = get_sessions_ref().get()
    today = datetime.now().strftime('%Y-%m-%d')
    sessions = list(data.values()) if data else []
    today_sessions = [s for s in sessions if s.get('connected_at', '').startswith(today)]
    total_today = len(today_sessions)
    avg_duration = int(sum(s.get('duration_seconds', 0) for s in today_sessions) / len(today_sessions)) if today_sessions else 0
    return jsonify({'total_today': total_today, 'avg_duration': avg_duration})

@app.route('/api/cards')
def get_cards():
    cards_data = get_cards_ref().get() or {}
    sessions_data = get_sessions_ref().get() or {}
    sessions = list(sessions_data.values()) if sessions_data else []
    result = []
    for uid, card in cards_data.items():
        card_sessions = [s for s in sessions if s.get('uid') == card.get('uid')]
        card['total_sessions'] = len(card_sessions)
        card['last_seen'] = max((s.get('connected_at', '') for s in card_sessions), default=None)
        result.append(card)
    result.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return jsonify(result)

@app.route('/api/cards', methods=['POST'])
def add_card():
    data = request.json
    uid = data['uid']
    key = uid.replace(' ', '_')
    cards_ref = get_cards_ref()
    existing = cards_ref.child(key).get()
    if existing:
        return jsonify({'success': False, 'error': 'Card already exists'}), 400
    cards_ref.child(key).set({
        'uid': uid,
        'name': data['name'],
        'created_at': datetime.now().isoformat()
    })
    return jsonify({'success': True})

@app.route('/api/cards/<path:uid>', methods=['PUT'])
def update_card(uid):
    data = request.json
    key = uid.replace(' ', '_')
    get_cards_ref().child(key).update({'name': data['name']})
    return jsonify({'success': True})

@app.route('/api/cards/<path:uid>', methods=['DELETE'])
def delete_card(uid):
    key = uid.replace(' ', '_')
    get_cards_ref().child(key).delete()
    return jsonify({'success': True})

@app.route('/api/arduino-status')
def get_arduino_status():
    data = get_arduino_ref().get() or {}
    last = data.get('last_signal')
    connected = data.get('connected', False)
    if last:
        diff = int((datetime.now() - datetime.fromisoformat(last)).total_seconds())
        last_signal_ago = str(diff) + 's ago'
    else:
        last_signal_ago = 'No signal yet'
    return jsonify({
        'connected': connected,
        'com_port': data.get('com_port', 'Unknown'),
        'baud_rate': 9600,
        'last_signal': last_signal_ago
    })

@app.route('/api/sessions/export')
def export_sessions():
    data = get_sessions_ref().get()
    sessions = list(data.values()) if data else []
    sessions.sort(key=lambda x: x.get('id', 0), reverse=True)
    csv = "ID,UID,Card Name,Connected At,Disconnected At,Duration (seconds)\n"
    for s in sessions:
        csv += f"{s.get('id')},{s.get('uid')},{s.get('card_name')},{s.get('connected_at')},{s.get('disconnected_at')},{s.get('duration_seconds')}\n"
    return Response(csv, mimetype='text/csv',
                    headers={"Content-Disposition": "attachment;filename=rfid_sessions.csv"})

@app.route('/api/sessions/clear', methods=['DELETE'])
def clear_sessions():
    get_sessions_ref().delete()
    return jsonify({'success': True})

# ── Agent endpoints (called by agent.py) ──────────────────
@app.route('/agent/card-event', methods=['POST'])
def card_event():
    data = request.json
    event = data.get('event')

    if event == 'connected':
        get_current_ref().set({
            'active': True,
            'uid': data['uid'],
            'card_name': data['card_name'],
            'connected_at': data['connected_at']
        })
        socketio.emit('card_connected', data)

    elif event == 'disconnected':
        # Save session to Firebase
        sessions_ref = get_sessions_ref()
        existing = sessions_ref.get() or {}
        new_id = len(existing) + 1
        sessions_ref.child(str(new_id)).set({
            'id': new_id,
            'uid': data['uid'],
            'card_name': data['card_name'],
            'connected_at': data['connected_at'],
            'disconnected_at': data['disconnected_at'],
            'duration_seconds': data['duration_seconds']
        })
        get_current_ref().set({'active': False})
        socketio.emit('card_disconnected', data)

    return jsonify({'success': True})

@app.route('/agent/arduino-status', methods=['POST'])
def agent_arduino_status():
    data = request.json
    get_arduino_ref().set(data)
    socketio.emit('arduino_status', {'connected': data.get('connected', False)})
    return jsonify({'success': True})

@app.route('/agent/card-name', methods=['POST'])
def get_card_name():
    uid = request.json.get('uid')
    key = uid.replace(' ', '_')
    card = get_cards_ref().child(key).get()
    return jsonify({'name': card['name'] if card else 'Unknown Card'})

# ── Main ──────────────────────────────────────────────────
if __name__ == '__main__':
    print("🚀 RFID Server running at http://localhost:5000")
    socketio.run(app, debug=False, host='0.0.0.0', port=5000)
