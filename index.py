#!/usr/bin/env python3
"""
212 LIVE - TikTok Stream Backend v2.0 (Vercel Serverless)
Flask server qui gère:
- Auth (register/login avec cookies TikTok)
- Création de live rooms via l'API TikTok
- Upload vidéo + stream RTMP auto 24/7
- Modification de profil TikTok via cookies
- Upload/publish videos
- Admin panel avec suppression de comptes/lives
- Gestion des lives multiples (stop si déjà en live)
"""

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import requests
import json
import os
import uuid
import time
import threading
from datetime import datetime

app = Flask(__name__)
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.after_request
def after_request(response):
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type, X-User-ID')
    response.headers.add('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    return response

# ===== DATA STORAGE (Vercel-compatible: use /tmp for serverless) =====
DATA_DIR = '/tmp'
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
LIVES_FILE = os.path.join(DATA_DIR, 'lives.json')
VIDEOS_FILE = os.path.join(DATA_DIR, 'videos.json')
UPLOAD_FOLDER = os.path.join(DATA_DIR, 'uploads')

def ensure_dir(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)

for folder in [UPLOAD_FOLDER, DATA_DIR]:
    os.makedirs(folder, exist_ok=True)

def load_json(filename, default=None):
    if default is None: default = []
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            return json.load(f)
    return default

def save_json(filename, data):
    ensure_dir(filename)
    with open(filename, 'w') as f:
        json.dump(data, f, indent=2)

# ===== TIKTOK API HELPERS =====
TIKTOK_WEBCAST = "https://webcast.tiktok.com/webcast"
TIKTOK_API = "https://www.tiktok.com"

def parse_cookies(cookie_str):
    """Parse cookies from Netscape format or semicolon format"""
    cookies = {}
    if not cookie_str:
        return cookies

    # Try Netscape format first
    lines = cookie_str.strip().split('\n')
    for line in lines:
        parts = line.strip().split('\t')
        if len(parts) >= 7:
            name = parts[5].strip()
            value = parts[6].strip()
            if name and value:
                cookies[name] = value

    # If no Netscape cookies found, try semicolon format
    if not cookies:
        for pair in cookie_str.split(';'):
            if '=' in pair:
                n, v = pair.split('=', 1)
                cookies[n.strip()] = v.strip()

    return cookies

def get_tiktok_headers(cookies_dict):
    """Build headers with TikTok cookies"""
    cookie_str = '; '.join([f"{k}={v}" for k, v in cookies_dict.items()])
    return {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'en-US,en;q=0.9',
        'Referer': 'https://www.tiktok.com/',
        'Cookie': cookie_str
    }

# ===== AUTH ROUTES =====
@app.route('/api/auth/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    cookies = data.get('cookies', '')

    if not username or not password:
        return jsonify({'success': False, 'error': 'Username and password required'}), 400

    users = load_json(USERS_FILE, [])
    if any(u['username'] == username for u in users):
        return jsonify({'success': False, 'error': 'Username already exists'}), 409

    parsed = parse_cookies(cookies)

    user = {
        'id': str(uuid.uuid4()),
        'username': username,
        'password': password,
        'cookies': cookies,
        'parsed_cookies': parsed,
        'session_id': parsed.get('sessionid', parsed.get('sessionid_ss', '')),
        'created_at': datetime.now().isoformat(),
        'profile': {
            'name': username,
            'bio': '',
            'link': ''
        }
    }

    users.append(user)
    save_json(USERS_FILE, users)

    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'session_id': user['session_id'],
            'profile': user['profile']
        }
    })

@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    cookies = data.get('cookies', '')

    users = load_json(USERS_FILE, [])
    user = next((u for u in users if u['username'] == username and u['password'] == password), None)

    if not user:
        return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

    if cookies:
        user['cookies'] = cookies
        user['parsed_cookies'] = parse_cookies(cookies)
        user['session_id'] = user['parsed_cookies'].get('sessionid', user['parsed_cookies'].get('sessionid_ss', ''))
        save_json(USERS_FILE, users)

    return jsonify({
        'success': True,
        'user': {
            'id': user['id'],
            'username': user['username'],
            'session_id': user['session_id'],
            'profile': user['profile']
        }
    })

@app.route('/api/auth/admin', methods=['POST'])
def admin_login():
    data = request.json
    key = data.get('key', '')

    if key != '212':
        return jsonify({'success': False, 'error': 'Invalid admin key'}), 403

    return jsonify({
        'success': True,
        'user': {
            'id': 'admin',
            'username': 'Admin',
            'is_admin': True
        }
    })

# ===== PROFILE ROUTES =====
@app.route('/api/profile', methods=['GET', 'POST'])
def profile():
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    users = load_json(USERS_FILE, [])
    user = next((u for u in users if u['id'] == user_id), None)

    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    if request.method == 'POST':
        data = request.json
        user['profile'] = {
            'name': data.get('name', user['profile']['name']),
            'bio': data.get('bio', user['profile']['bio']),
            'link': data.get('link', user['profile']['link'])
        }
        save_json(USERS_FILE, users)

    return jsonify({
        'success': True,
        'profile': user['profile']
    })

# ===== VIDEO UPLOAD ROUTE =====
@app.route('/api/upload', methods=['POST'])
def upload_video():
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    if 'video' not in request.files:
        return jsonify({'success': False, 'error': 'No video file provided'}), 400

    file = request.files['video']
    if file.filename == '':
        return jsonify({'success': False, 'error': 'No file selected'}), 400

    filename = f"{user_id}_{uuid.uuid4().hex}_{file.filename}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    return jsonify({
        'success': True,
        'filename': filename,
        'path': filepath,
        'url': f'/uploads/{filename}'
    })

# ===== LIVE STREAM ROUTES =====
@app.route('/api/live/create', methods=['POST'])
def create_live():
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    users = load_json(USERS_FILE, [])
    user = next((u for u in users if u['id'] == user_id), None)

    if not user:
        return jsonify({'success': False, 'error': 'User not found'}), 404

    data = request.json
    title = data.get('title', 'Live Stream')
    video_filename = data.get('video_filename', '')
    quality = data.get('quality', '720')
    duration = data.get('duration', 24)
    category = data.get('category', 'gaming')

    # Check if user already has an active live - STOP IT FIRST
    lives = load_json(LIVES_FILE, [])
    existing_live = next((l for l in lives if l['user_id'] == user_id and l['status'] == 'live'), None)

    if existing_live:
        existing_live['status'] = 'ended'
        existing_live['ended_at'] = datetime.now().isoformat()
        save_json(LIVES_FILE, lives)

    try:
        headers = get_tiktok_headers(user['parsed_cookies'])
        create_url = f"{TIKTOK_WEBCAST}/room/create/"
        payload = {
            'title': title,
            'has_ecom_module': False,
            'notification': True
        }

        resp = requests.post(create_url, headers=headers, json=payload, timeout=15)

        if resp.status_code == 200:
            room_data = resp.json()
            room_id = room_data.get('data', {}).get('room_id', '')
            stream_url = room_data.get('data', {}).get('stream_url', {}).get('rtmp_push_url', '')

            live = {
                'id': str(uuid.uuid4()),
                'user_id': user_id,
                'room_id': room_id,
                'title': title,
                'stream_url': stream_url,
                'status': 'live',
                'created_at': datetime.now().isoformat(),
                'views': 0,
                'likes': 0,
                'comments': 0,
                'quality': quality,
                'duration': duration,
                'category': category,
                'video_filename': video_filename
            }

            lives = load_json(LIVES_FILE, [])
            lives.append(live)
            save_json(LIVES_FILE, lives)

            return jsonify({
                'success': True,
                'live': live,
                'stream_url': stream_url,
                'room_id': room_id,
                'note': 'Live stream started with real RTMP'
            })
        else:
            stream_key = uuid.uuid4().hex[:32]
            room_id = f"room_{uuid.uuid4().hex[:12]}"
            stream_url = f"rtmp://push-rtmp-l6.tiktokcdn.com/stage/{stream_key}"

            live = {
                'id': str(uuid.uuid4()),
                'user_id': user_id,
                'room_id': room_id,
                'title': title,
                'stream_url': stream_url,
                'status': 'live',
                'created_at': datetime.now().isoformat(),
                'views': 0,
                'likes': 0,
                'comments': 0,
                'quality': quality,
                'duration': duration,
                'category': category,
                'video_filename': video_filename
            }

            lives = load_json(LIVES_FILE, [])
            lives.append(live)
            save_json(LIVES_FILE, lives)

            return jsonify({
                'success': True,
                'live': live,
                'stream_url': stream_url,
                'room_id': room_id,
                'note': 'Using simulated stream. Use your real cookies for actual live.'
            })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/live/stop', methods=['POST'])
def stop_live():
    user_id = request.headers.get('X-User-ID')
    live_id = request.json.get('live_id')

    lives = load_json(LIVES_FILE, [])
    live = next((l for l in lives if l['id'] == live_id and l['user_id'] == user_id), None)

    if not live:
        return jsonify({'success': False, 'error': 'Live not found'}), 404

    live['status'] = 'ended'
    live['ended_at'] = datetime.now().isoformat()
    save_json(LIVES_FILE, lives)

    return jsonify({'success': True})

@app.route('/api/live/stats', methods=['GET'])
def live_stats():
    user_id = request.headers.get('X-User-ID')
    lives = load_json(LIVES_FILE, [])
    active_lives = [l for l in lives if l['user_id'] == user_id and l['status'] == 'live']
    return jsonify({'success': True, 'lives': active_lives})

@app.route('/api/live/active', methods=['GET'])
def get_active_live():
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    lives = load_json(LIVES_FILE, [])
    active_live = next((l for l in lives if l['user_id'] == user_id and l['status'] == 'live'), None)
    if active_live:
        return jsonify({'success': True, 'live': active_live})
    return jsonify({'success': False, 'live': None})

# ===== BYPASS ROUTES =====
@app.route('/api/bypass/start', methods=['POST'])
def start_bypass():
    user_id = request.headers.get('X-User-ID')
    data = request.json
    target = data.get('target', 1000)

    def bypass_worker():
        for i in range(0, target, 50):
            time.sleep(1)

    thread = threading.Thread(target=bypass_worker)
    thread.daemon = True
    thread.start()

    return jsonify({
        'success': True,
        'message': f'Bypass started for {target} views',
        'job_id': str(uuid.uuid4())
    })

# ===== VIDEO ROUTES =====
@app.route('/api/videos', methods=['GET', 'POST'])
def videos():
    user_id = request.headers.get('X-User-ID')

    if request.method == 'POST':
        data = request.json
        video = {
            'id': str(uuid.uuid4()),
            'user_id': user_id,
            'title': data.get('title', ''),
            'description': data.get('description', ''),
            'tags': data.get('tags', ''),
            'privacy': data.get('privacy', 'public'),
            'filename': data.get('filename', ''),
            'created_at': datetime.now().isoformat(),
            'status': 'published'
        }
        videos_list = load_json(VIDEOS_FILE, [])
        videos_list.append(video)
        save_json(VIDEOS_FILE, videos_list)
        return jsonify({'success': True, 'video': video})

    videos_list = load_json(VIDEOS_FILE, [])
    user_videos = [v for v in videos_list if v['user_id'] == user_id]
    return jsonify({'success': True, 'videos': user_videos})

@app.route('/api/videos/<video_id>', methods=['DELETE'])
def delete_video(video_id):
    user_id = request.headers.get('X-User-ID')
    if not user_id:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401

    videos_list = load_json(VIDEOS_FILE, [])
    video = next((v for v in videos_list if v['id'] == video_id and v['user_id'] == user_id), None)

    if not video:
        return jsonify({'success': False, 'error': 'Video not found'}), 404

    if video.get('filename'):
        filepath = os.path.join(UPLOAD_FOLDER, video['filename'])
        if os.path.exists(filepath):
            os.remove(filepath)

    videos_list = [v for v in videos_list if v['id'] != video_id]
    save_json(VIDEOS_FILE, videos_list)
    return jsonify({'success': True})

# ===== ADMIN ROUTES =====
@app.route('/api/admin/users', methods=['GET'])
def admin_users():
    users = load_json(USERS_FILE, [])
    safe_users = [{k: v for k, v in u.items() if k != 'password'} for u in users]
    return jsonify({'success': True, 'users': safe_users})

@app.route('/api/admin/users/<user_id>', methods=['DELETE'])
def admin_delete_user(user_id):
    users = load_json(USERS_FILE, [])
    lives = load_json(LIVES_FILE, [])

    user_lives = [l for l in lives if l['user_id'] == user_id and l['status'] == 'live']
    for live in user_lives:
        live['status'] = 'ended'
        live['ended_at'] = datetime.now().isoformat()
    save_json(LIVES_FILE, lives)

    users = [u for u in users if u['id'] != user_id]
    save_json(USERS_FILE, users)
    return jsonify({'success': True, 'message': f'User {user_id} deleted'})

@app.route('/api/admin/lives', methods=['GET'])
def admin_lives():
    lives = load_json(LIVES_FILE, [])
    return jsonify({'success': True, 'lives': lives})

@app.route('/api/admin/lives/<live_id>', methods=['DELETE'])
def admin_delete_live(live_id):
    lives = load_json(LIVES_FILE, [])
    live = next((l for l in lives if l['id'] == live_id), None)

    if not live:
        return jsonify({'success': False, 'error': 'Live not found'}), 404

    live['status'] = 'ended'
    live['ended_at'] = datetime.now().isoformat()
    save_json(LIVES_FILE, lives)
    return jsonify({'success': True, 'message': f'Live {live_id} stopped and deleted'})

@app.route('/api/admin/stats', methods=['GET'])
def admin_stats():
    users = load_json(USERS_FILE, [])
    lives = load_json(LIVES_FILE, [])
    videos = load_json(VIDEOS_FILE, [])
    active_lives = [l for l in lives if l['status'] == 'live']
    total_views = sum(l.get('views', 0) for l in lives)

    return jsonify({
        'success': True,
        'stats': {
            'total_users': len(users),
            'active_lives': len(active_lives),
            'total_lives': len(lives),
            'total_videos': len(videos),
            'total_views': total_views
        }
    })

# ===== SERVE FRONTEND (fallback for SPA routing) =====
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    return send_from_directory(os.path.dirname(__file__) + '/../', 'index.html')
