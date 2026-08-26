import requests
from requests.adapters import HTTPAdapter
from urllib.parse import urlparse
import time
import os

class CustomResolverAdapter(HTTPAdapter):
    def send(self, request, **kwargs):
        url = urlparse(request.url)
        if 'agiliumtrade.ai' in url.netloc:
            try:
                doh_url = f"https://cloudflare-dns.com/dns-query?name={url.netloc}&type=A"
                headers = {"Accept": "application/dns-json"}
                r = requests.get(doh_url, headers=headers, timeout=5)
                data = r.json()
                
                if "Answer" in data:
                    ip_address = data["Answer"][0]["data"]
                    request.headers['Host'] = url.netloc
                    request.url = request.url.replace(url.netloc, ip_address)
            except Exception as e:
                print(f"DNS bypass info: {e}")
        return super().send(request, **kwargs)

_session = requests.Session()
_adapter = CustomResolverAdapter()
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

requests.get = _session.get
requests.post = _session.post
requests.put = _session.put
requests.delete = _session.delete

TOKEN = os.getenv('METAPI_TOKEN', 'tvj_token')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID', 'tvj_account_id')

print("Bot štartuje a obchádza DNS cez Cloudflare HTTPS...")

while True:
    try:
        url = f"https://mt-client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/trade-account/positions"
        headers = {'auth-token': TOKEN}
        response = requests.get(url, headers=headers, timeout=10)
        print("Stav pripojenia:", response.status_code, response.text)
    except Exception as e:
        print("Chyba:", e)
    
    time.sleep(10)
