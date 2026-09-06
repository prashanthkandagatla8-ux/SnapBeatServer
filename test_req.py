import requests

try:
    with open("run.py", "rb") as f:
        files = {
            "audio": ("run.py", f, "audio/mpeg"),
            "photos": ("run.py", f, "image/jpeg")
        }
        resp = requests.post("http://127.0.0.1:8772/api/render/mobile", files=files)
        print("STATUS:", resp.status_code)
        print("BODY:", resp.text)
except Exception as e:
    print("ERROR:", e)
