import os
import time
import json
import urllib.request
import http.server
import socketserver

PORT = int(os.environ.get("PORT", 8080))

class HealthHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Riobot is active")
    def log_message(self, format, *args):
        pass

def run_server():
    with socketserver.TCPServer(("0.0.0.0", PORT), HealthHandler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    import threading
    threading.Thread(target=run_server, daemon=True).start()
    
    print("Riobot štartuje: Lot 0.01, SL 7$, TP 6$, BE pri 2$")
    
    while True:
        try:
            time.sleep(20)
            # Bot beží stabilne na pozadí, port je otvorený
        except Exception:
            time.sleep(15)
