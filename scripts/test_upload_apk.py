import os
import urllib.request
import json
from pathlib import Path

def test_upload():
    token = '8899084854:AAEAbX7_GhivaVFoFGwpG_2JLLeJ1JRYhoE'
    url = f'https://api.telegram.org/bot{token}/sendDocument'
    file_path = Path('downloads/ChildSecurity_Parent.apk')

    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    body = []
    body.extend([
        f'--{boundary}'.encode(),
        b'Content-Disposition: form-data; name="chat_id"',
        b'',
        b'6707839718',
    ])
    body.extend([
        f'--{boundary}'.encode(),
        b'Content-Disposition: form-data; name="caption"',
        b'',
        'Child Security Parent APK'.encode('utf-8'),
    ])
    body.extend([
        f'--{boundary}'.encode(),
        f'Content-Disposition: form-data; name="document"; filename="{file_path.name}"'.encode(),
        b'Content-Type: application/vnd.android.package-archive',
        b'',
        file_path.read_bytes(),
    ])
    body.extend([f'--{boundary}--'.encode(), b''])
    data = b'\r\n'.join(body)

    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}
    )
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=120).read())
        print('Upload OK:', res.get('ok'))
        if res.get('ok'):
            file_id = res['result']['document']['file_id']
            print('Parent APK file_id:', file_id)
            return file_id
    except urllib.error.HTTPError as e:
        print('Error:', e.read().decode())
    return None

if __name__ == '__main__':
    test_upload()
