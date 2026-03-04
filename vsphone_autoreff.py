import os
import re
import json
import time
import hmac
import hashlib
import random
import string
import requests
import logging
import numpy as np
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from urllib.parse import urlencode

# === TRY OCR DEPS (safe fallback) ===
try:
    import cv2
    from PIL import Image
    import pytesseract
    OCR_OK = True
except ImportError:
    OCR_OK = False
    cv2 = None
    Image = None
    pytesseract = None

# ============================================================
#  CONFIG — ISI LANGSUNG DI SINI
# ============================================================
ACCESS_KEY = "PS9jcJCkqIYi79PnOzXoEFDrPxsfXOXB"  # GANTI
SECRET_KEY = "iugve27EONOZ9Hl1JvvYEWKa"          # GANTI
HOST       = "api.vsphone.com"                   # JANGAN DIGANTI KECUALI ADA PERINTAH
PAD_CODES  = [
    "APP5AV4BTI6XWCGG",  # GANTI SESUAI PUNYA KAMU
    "APP5BT4QV9UVNUAW",  # GANTI SESUAI PUNYA KAMU
]

ACCOUNTS_TARGET = 5  # BERAPA BANYAK AKUN YANG INGIN DIBUAT
REFF_PER_MASTER = 5
AKUN_PER_VSP    = 2

APK_URL     = "https://statistic.topnod.com/TopNod.apk"
APK_LOCAL   = "/sdcard/Download/TopNod.apk"
OUTPUT_FILE = "akun_topnod.json"

# ============================================================
#  LOGGING
# ============================================================
logging.basicConfig(    level=logging.INFO,
    format="[%(asctime)s] %(levelname)-5s %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("topnod")

def loginfo(msg): log.info(msg)
def logerr(msg):  log.error(f"❌ {msg}")

# ============================================================
#  VSPHONE API — AK/SK AUTH
# ============================================================
def _sign_request(method, path, params=None, body=None):
    timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
    nonce     = ''.join(random.choices(string.ascii_lowercase + string.digits, k=16))

    canonical_parts = [method.upper(), path, timestamp, nonce]
    if params:
        sorted_params = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        canonical_parts.append(sorted_params)
    if body:
        body_str = json.dumps(body, separators=(',', ':'), sort_keys=True)
        canonical_parts.append(body_str)

    canonical = "\n".join(canonical_parts)
    signature = hmac.new(
        SECRET_KEY.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    return {
        "X-Access-Key"  : ACCESS_KEY,
        "X-Timestamp"   : timestamp,
        "X-Nonce"       : nonce,
        "X-Signature"   : signature,
        "Content-Type"  : "application/json",
    }

def api(endpoint, payload=None, method="POST"):
    url = f"https://{HOST}{endpoint}"
    headers = _sign_request(method, endpoint, body=payload)
    try:
        r = requests.request(method, url, headers=headers, json=payload or {}, timeout=30)
        data = r.json() if r.text else {}
        code = data.get("code") or data.get("status") or data.get("retCode")
        if str(code) in ("200", "0", "success"):
            return data.get("data") or data.get("result") or data
        else:
            msg = data.get("msg") or data.get("message") or "unknown"            logerr(f"API {endpoint}: {code} | {msg}")
            return None
    except Exception as e:
        logerr(f"Req {endpoint}: {e}")
        return None

# ── Device & App ───────────────────────────────────────────
def clear_app(pad_code, pkg):
    api("/vsphone/api/padApi/asyncCmd", {"padCodes": [pad_code], "cmd": f"pm clear {pkg}"})
    time.sleep(2)

def open_app(pad_code, pkg):
    api("/vsphone/api/padApi/asyncCmd", {"padCodes": [pad_code], "cmd": f"monkey -p {pkg} 1"})
    time.sleep(4)

def get_package_name(pad_code):
    res = api("/vsphone/api/padApi/asyncCmd", {"padCodes": [pad_code], "cmd": "pm list packages | grep -i topnod"})
    time.sleep(2)
    if res:
        m = re.search(r'package:([\w.]+)', str(res))
        return m.group(1) if m else "com.topnod.app"
    return "com.topnod.app"

def tap(pad_code, x, y):
    api("/vsphone/api/padApi/simulateTouch", {"padCode": pad_code, "x": x, "y": y, "eventType": 0})
    time.sleep(random.uniform(1.5, 2.5))

def swipe(pad_code, x1, y1, x2, y2, dur=800):
    api("/vsphone/api/padApi/simulateTouch", {
        "padCode": pad_code, "startX": x1, "startY": y1, "endX": x2, "endY": y2,
        "duration": dur, "eventType": 1
    })
    time.sleep(1)

def input_text(pad_code, text):
    esc = text.replace("'", "'\"'\"'").replace(" ", "%s")
    api("/vsphone/api/padApi/asyncCmd", {"padCodes": [pad_code], "cmd": f"input text '{esc}'"})
    time.sleep(1)

def read_clipboard(pad_code):
    res = api("/vsphone/api/padApi/asyncCmd", {"padCodes": [pad_code], "cmd": "dumpsys clipboard 2>/dev/null | grep -o 'text=[^ ]*' | head -1"})
    time.sleep(2)
    if res:
        m = re.search(r'text=([A-Z0-9_]{6,25})', str(res))
        return m.group(1) if m else None
    return None

# ── Screenshot & OCR (fallback safe) ───────────────────────
def get_screenshot(pad_code):
    res = api("/vsphone/api/padApi/getLongGenerateUrl", {"padCodes": [pad_code]})    if not res: return None
    try:
        url = res[0].get("url") if isinstance(res, list) else res.get("url")
        if not url: return None
        r = requests.get(url, timeout=15)
        arr = np.frombuffer(r.content, dtype=np.uint8)
        return cv2.imdecode(arr, cv2.IMREAD_COLOR) if cv2 else None
    except: return None

def ocr_region(img, x, y, w, h, cfg="--psm 6"):
    if not OCR_OK or img is None or cv2 is None: return ""
    try:
        crop = img[y:y+h, x:x+w]
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        scaled = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
        _, th = cv2.threshold(scaled, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        return pytesseract.image_to_string(Image.fromarray(th), config=cfg)
    except: return ""

def get_spins_left(pad_code):
    screen = get_screenshot(pad_code)
    if screen 1
    text = ocr_region(screen, 250, 580, 200, 100, "--psm 7")
    m = re.search(r'(\d+)\s*left', text, re.IGNORECASE)
    return int(m.group(1)) if m else 0

# ── Captcha Solver ─────────────────────────────────────────
def solve_captcha(pad_code):
    BG_X, BG_Y, BG_W, BG_H = 165, 535, 370, 440
    PIECE_X, PIECE_Y = 90, 760
    SLIDER_X, SLIDER_Y = 137, 1053

    for attempt in range(3):
        screen = get_screenshot(pad_code)
        if screen is None:
            time.sleep(2)
            continue

        gap_x = BG_X + (BG_W // 2)  # fallback: tengah
        try:
            bg_crop = screen[BG_Y:BG_Y+BG_H, BG_X:BG_X+BG_W]
            if cv2:
                gray = cv2.cvtColor(bg_crop, cv2.COLOR_BGR2GRAY)
                edges = cv2.Canny(gray, 30, 100)
                cnts, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                for c in cnts:
                    if 400 < cv2.contourArea(c) < 2000:
                        x, _, w, _ = cv2.boundingRect(c)
                        if x > BG_X:
                            gap_x = BG_X + x + w//2                            break
        except: pass

        distance = gap_x - PIECE_X + random.randint(-4, 4)
        loginfo(f"Captcha: gap={gap_x}, swipe={distance}")

        cur = SLIDER_X
        for i in range(8):
            t = i / 7
            ease = 3*t*t - 2*t*t*t
            step = int(ease * distance)
            next_x = cur + step
            swipe(pad_code, cur, SLIDER_Y, next_Y, int(60 + 50 * abs(0.5 - t)))
            cur = next_x
        time.sleep(2.5)

        # Verifikasi: cek apakah area captcha berubah
        screen2 = get_screenshot(pad_code)
        if screen2 is not None:
            try:
                diff = cv2.absdiff(
                    cv2.resize(screen[BG_Y:BG_Y+BG_H, BG_X:BG_X+BG_W], (100, 50)),
                    cv2.resize(screen2[BG_Y:BG_Y+BG_H, BG_X:BG_X+BG_W], (100, 50))
                )
                if diff.mean() > 10:
                    loginfo("✅ Captcha solved!")
                    return True
            except: pass
        time.sleep(1.5)

    logerr("Captcha gagal")
    return False

# ── KUKU.LU EMAIL ─────────────────────────────────────────
_KUKULU_BASE = "https://m.kuku.lu"
_sess = requests.Session()
_sess.headers.update({"User-Agent": "Mozilla/5.0"})

def get_temp_email():
    user = ''.join(random.choices(string.ascii_lowercase, k=8)) + ''.join(random.choices(string.digits, k=4))
    domain = "boxfi.uk"
    try:
        _sess.post(f"{_KUKULU_BASE}/create.php", data={"address": user, "domain": domain}, timeout=5)
    except: pass
    email = f"{user}@{domain}"
    loginfo(f"📧 {email}")
    return email, {"user": user, "domain": domain}

def check_inbox(meta, timeout=60):
    user, dom = meta["user"], meta["domain"]    start = time.time()
    while time.time() - start < timeout:
        try:
            r = _sess.get(f"{_KUKULU_BASE}/inbox.php", params={"address": user, "domain": dom}, timeout=5)
            soup = BeautifulSoup(r.text, "html.parser")
            link = soup.select_one("div.mail a[href]")
            if link:
                href = link["href"]
                if not href.startswith("http"): href = f"{_KUKULU_BASE}/{href}"
                r2 = _sess.get(href, timeout=5)
                body = soup.find("div", class_=re.compile("body|content"))
                txt = body.get_text(" ", strip=True) if body else r2.text
                return txt
        except: pass
        time.sleep(4)
    return None

def extract_otp(txt):
    m = re.findall(r'\b\d{4,6}\b', txt or "")
    return m[0] if m else None

# ============================================================
#  UI COORDINATES (720px device)
# ============================================================
UI = {
    "email": (353, 490),
    "otp": (353, 660),
    "reff": (353, 835),
    "next": (353, 1007),
    "pass": (353, 620),
    "confirm": (353, 880),
    "continue": (353, 1355),
    "skip": (611, 140),
    "event": (353, 210),
    "spin": (353, 660),
    "claim": (551, 1330),
    "ok": (353, 955),
    "invite": (563, 1023),
    "copy": (463, 1152),
    "close": (637, 670),
}

def gen_pass(): return _rand_str(6).capitalize() + _rand_num(4) + "!"
def _rand_str(n): return ''.join(random.choices(string.ascii_lowercase, k=n))
def _rand_num(n): return ''.join(random.choices(string.digits, k=n))

# ============================================================
#  CORE: REGISTER + SPIN
# ============================================================
def register_and_spin(pad_code, pkg, reff_code=""):    clear_app(pad_code, pkg)
    open_app(pad_code, pkg)
    time.sleep(5)

    # Step 1: Email
    tap(pad_code, *UI["email"])
    email, meta = get_temp_email()
    input_text(pad_code, email)
    tap(pad_code, *UI["next"])
    time.sleep(3)

    # Step 2: OTP
    body = check_inbox(meta, timeout=90)
    otp = extract_otp(body)
    if not otp:
        logerr("OTP tidak ditemukan")
        return False
    tap(pad_code, *UI["otp"])
    input_text(pad_code, otp)
    tap(pad_code, *UI["next"])
    time.sleep(3)

    # Step 3: Referral
    if reff_code:
        tap(pad_code, *UI["reff"])
        input_text(pad_code, reff_code)
    tap(pad_code, *UI["next"])
    time.sleep(3)

    # Step 4: Password
    pwd = gen_pass()
    tap(pad_code, *UI["pass"])
    input_text(pad_code, pwd)
    tap(pad_code, *UI["confirm"])
    input_text(pad_code, pwd)
    tap(pad_code, *UI["continue"])
    time.sleep(3)

    # Skip biometric
    tap(pad_code, *UI["skip"])
    time.sleep(2)

    # Claim & Spin
    tap(pad_code, *UI["event"])
    time.sleep(3)
    tap(pad_code, *UI["claim"])
    time.sleep(2)
    tap(pad_code, *UI["ok"])
    time.sleep(2)
    tap(pad_code, *UI["spin"])    time.sleep(5)
    tap(pad_code, 353, 800)  # close result
    time.sleep(1)

    # Save
    save_account({"email": email, "password": pwd, "reff_code": reff_code})
    loginfo(f"✅ Akun selesai: {email}")
    return True

def save_account(data):
    try:
        accs = json.load(open(OUTPUT_FILE)) if os.path.exists(OUTPUT_FILE) else []
        accs.append(data)
        with open(OUTPUT_FILE, "w") as f:
            json.dump(accs, f, indent=2)
    except Exception as e:
        logerr(f"Simpan gagal: {e}")

# ============================================================
#  MAIN LOOP — JALAN LANGSUNG
# ============================================================
if __name__ == "__main__":
    loginfo("🔥 Mulai Auto-Reff Bot")

    if not ACCESS_KEY or not SECRET_KEY:
        logerr("❌ ACCESS_KEY / SECRET_KEY belum diisi di kode!")
        exit(1)
    if not PAD_CODES:
        logerr("❌ PAD_CODES kosong! Isi di kode.")
        exit(1)

    pkg = "com.topnod.app"
    master_code = None

    # Buat akun master dulu (1 akun utama)
    loginfo("📦 Membuat akun master...")
    if register_and_spin(PAD_CODES[0], pkg):
        master_code = get_reff_code(PAD_CODES[0])
        if not master_code:
            logerr("Gagal ambil referral code master — abort")
            exit(1)
        loginfo(f"🔑 Referral master: {master_code}")

    # Buat akun reff (5 akun per master)
    for i in range(min(ACCOUNTS_TARGET, len(PAD_CODES))):
        pad = PAD_CODES[i + 1] if i + 1 < len(PAD_CODES) else PAD_CODES[0]
        loginfo(f"🔄 Buat akun ke-{i+1} di pad {pad}")
        register_and_spin(pad, pkg, master_code)
        time.sleep(10)
    loginfo("🎉 Selesai. Akun tersimpan di akun_topnod.json")
