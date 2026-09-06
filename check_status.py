import requests

try:
    resp = requests.get("http://127.0.0.1:8772/api/render/status/17")
    print("STATUS:", resp.status_code)
    print("BODY:", resp.text)
except Exception as e:
    print("ERROR:", e)
