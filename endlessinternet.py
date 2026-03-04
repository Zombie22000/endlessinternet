import socket
import threading
import select
import struct
import requests
import os
import hashlib
from urllib.parse import urlparse, quote, unquote, urlencode, parse_qsl

# ====================== CONFIGURATION ======================
PROXY_PORT = 8080
POLL_ENDPOINT = "https://gen.pollinations.ai/v1/chat/completions"
CACHE_DIR = "endless_cache"
os.makedirs(CACHE_DIR, exist_ok=True)

ALLOWED_WITHOUT_KEY = {"pollinations.ai", "github.com", "google.com"}

def get_cache_path(url):
    url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()
    return os.path.join(CACHE_DIR, f"{url_hash}.html")

def sanitize_url_for_ai(url):
    """Removes the API key from the URL so the AI never sees it."""
    u = urlparse(url)
    query = dict(parse_qsl(u.query))
    query.pop('__fakeweb__apikey', None) # Remove the key parameter
    
    # Rebuild URL without the key
    new_query = urlencode(query)
    sanitized = f"{u.scheme}://{u.netloc}{u.path}"
    if new_query:
        sanitized += f"?{new_query}"
    return sanitized

def generate_settings_page(origin_url=""):
    target = origin_url if origin_url else "http://www.google.com/"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>Endless Internet Settings</title><script src="https://cdn.tailwindcss.com"></script></head>
<body class="bg-black text-white min-h-screen flex items-center justify-center p-6 font-sans">
  <div class="w-full max-w-md bg-zinc-900 p-8 rounded-3xl border border-zinc-800 shadow-2xl">
    <h1 class="text-4xl font-black mb-2 bg-gradient-to-r from-blue-400 to-purple-500 bg-clip-text text-transparent italic text-center">Endless Internet</h1>
    <p class="text-zinc-500 mb-8 text-xs uppercase tracking-[0.2em] font-bold text-center">Terminal Configuration</p>
    <div class="space-y-6">
        <div class="space-y-2">
            <label class="text-xs font-bold text-zinc-400 ml-1">POLLINATIONS API KEY</label>
            <input type="password" id="apikey" placeholder="sk_..." 
                   class="w-full p-4 bg-zinc-800 border border-zinc-700 rounded-2xl focus:ring-2 focus:ring-blue-500 outline-none transition-all text-blue-400 font-mono">
        </div>
        <button id="saveBtn" onclick="saveAndRedirect()" class="w-full py-4 bg-white text-black hover:bg-zinc-200 rounded-2xl font-black transition-all active:scale-95">SAVE & SURF</button>
        <p id="status" class="text-center text-zinc-600 text-[10px] uppercase tracking-widest mt-4"></p>
    </div>
    <script>
        const savedKey = localStorage.getItem('pollinations_apikey');
        const targetUrl = "{target}";
        if(savedKey) {{
            document.getElementById('apikey').value = savedKey;
            if(targetUrl && !targetUrl.includes('proxy.settings')) {{
                document.getElementById('status').innerText = "Key active. Porting...";
                setTimeout(() => {{ saveAndRedirect(); }}, 300); 
            }}
        }}
        function saveAndRedirect() {{
            const key = document.getElementById('apikey').value.trim();
            if(!key) return alert("API Key Required.");
            localStorage.setItem('pollinations_apikey', key);
            document.cookie = "pollinations_apikey=" + encodeURIComponent(key) + "; path=/; max-age=31536000";
            let base = targetUrl;
            let separator = base.includes('?') ? '&' : '?';
            window.location.href = base + separator + "__fakeweb__apikey=" + encodeURIComponent(key);
        }}
    </script>
  </div>
</body></html>"""
    return html.encode('utf-8')

def generate_fake_page(full_url, api_key):
    # CRITICAL: Clean the URL so the AI doesn't see its own key
    clean_url = sanitize_url_for_ai(full_url)
    domain = urlparse(clean_url).netloc
    seed = os.urandom(4).hex()
    
    prompt = f"""
    Rules for the ai:
    - ONLY output raw HTML code
    - Start with <!DOCTYPE html>
    - Use Tailwind CDN
    - Realistic layout doesnt matter
    - Internal relative links
    - External real links ok
    - Images via https://image.pollinations.ai/prompt/[description]?model=gptimage&width=1280&height=720&seed={seed}&nologo=true
    - Favicon via https://image.pollinations.ai/prompt/favicon_for_{domain}?model=gptimage&width=64&height=64&nologo=true
    - Pure HTML only
    
    Target Site: {clean_url}
    """
    
    try:
        r = requests.post(POLL_ENDPOINT, json={
            "model": "claude-fast", 
            "messages": [{"role": "user", "content": prompt}]
        }, headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"}, timeout=60)
        content = r.json()["choices"][0]["message"]["content"].strip()
        if "```" in content:
            content = content.split("```")[-2].replace("html", "", 1).strip()
        return content.encode('utf-8')
    except Exception as e:
        print(f"[*] AI Error: {e}")
        return None

def pipe(src, dst):
    try:
        while True:
            r, _, _ = select.select([src, dst], [], [], 20)
            if src in r:
                data = src.recv(32768)
                if not data: break
                dst.sendall(data)
            if dst in r:
                data = dst.recv(32768)
                if not data: break
                src.sendall(data)
    except: pass

def handle_client(client_socket):
    try:
        client_socket.recv(262)
        client_socket.sendall(b"\x05\x00")
        data = client_socket.recv(4)
        if not data: return
        _, cmd, _, atyp = struct.unpack('!BBBB', data)
        if atyp == 1: addr = socket.inet_ntoa(client_socket.recv(4))
        elif atyp == 3: addr = client_socket.recv(client_socket.recv(1)[0]).decode()
        else: return
        port = struct.unpack('!H', client_socket.recv(2))[0]

        if port == 443:
            try:
                remote = socket.create_connection((addr, port), timeout=5)
                client_socket.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
                pipe(client_socket, remote)
            except: client_socket.sendall(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
            return

        client_socket.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        request = client_socket.recv(32768)
        if not request: return
        req_lines = request.decode(errors='ignore').split('\r\n')
        path = req_lines[0].split(' ')[1] if len(req_lines[0].split(' ')) > 1 else "/"
        host = addr
        api_key = None
        for line in req_lines:
            if line.lower().startswith('host:'): host = line.split(':', 1)[1].strip()
            if 'pollinations_apikey=' in line: api_key = line.split('pollinations_apikey=')[1].split(';')[0].strip()
        
        if "__fakeweb__apikey=" in path:
            api_key = unquote(path.split("__fakeweb__apikey=")[1].split("&")[0])

        if "proxy.settings" in host:
            origin = unquote(path.split("origin=")[1].split("&")[0]) if "origin=" in path else ""
            res = generate_settings_page(origin)
            client_socket.sendall(f"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: {len(res)}\r\nConnection: close\r\n\r\n".encode() + res)
            return

        full_url = f"http://{host}{path}"
        cache_path = get_cache_path(full_url)
        if os.path.exists(cache_path):
            with open(cache_path, "rb") as f: content = f.read()
        else:
            if not api_key and host not in ALLOWED_WITHOUT_KEY:
                redir = f"http://proxy.settings/apikey?origin={quote(full_url)}"
                client_socket.sendall(f"HTTP/1.1 302 Found\r\nLocation: {redir}\r\nConnection: close\r\n\r\n".encode())
                return
            content = generate_fake_page(full_url, api_key) or b"<h1>pro-oh wait I mean pollinations exploded<h1>"
            with open(cache_path, "wb") as f: f.write(content)

        header = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(content)}\r\nConnection: close\r\n\r\n"
        client_socket.sendall(header.encode() + content)
    except: pass
    finally: client_socket.close()

def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', PROXY_PORT))
    server.listen(100)
    print(f"[*] Endless Internet Live: 127.0.0.1:{PROXY_PORT}")
    while True:
        client, _ = server.accept()
        threading.Thread(target=handle_client, args=(client,), daemon=True).start()

if __name__ == "__main__": main()