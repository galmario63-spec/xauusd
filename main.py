import dns.resolver
import requests
from requests.adapters import HTTPAdapter
from urllib.parse import urlparse

class CustomResolverAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        self.resolver = dns.resolver.Resolver()
        self.resolver.nameservers = ['1.1.1.1', '8.8.8.8']
        return super().init_poolmanager(*args, **kwargs)

    def send(self, request, **kwargs):
        url = urlparse(request.url)
        if 'agiliumtrade.ai' in url.netloc:
            try:
                answers = self.resolver.resolve(url.netloc, 'A')
                ip_address = answers[0].to_text()
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

import time
import os

# MetaApi token a ID účtu z premenných prostredia Railway
TOKEN = os.getenv('METAPI_TOKEN', 'tvj_token')
ACCOUNT_ID = os.getenv('METAPI_ACCOUNT_ID', 'tvj_account_id')

print("Bot štartuje a obchádza DNS cez Cloudflare...")

while True:
    try:
        url = f"https://mt-client-api-v1.agiliumtrade.ai/users/current/accounts/{ACCOUNT_ID}/trade-account/positions"
        headers = {'auth-token': TOKEN}
        response = requests.get(url, headers=headers, timeout=10)
        print("Stav pripojenia:", response.status_code, response.text)
    except Exception as e:
        print("Chyba:", e)
    
    time.sleep(10)
