import urllib.request
import json
from pathlib import Path

token = '8899084854:AAEAbX7_GhivaVFoFGwpG_2JLLeJ1JRYhoE'
url = f'https://api.telegram.org/bot{token}/sendDocument'

def upload(filename, caption):
    file_path = Path(f'downloads/{filename}')
    boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'
    body = [
        f'--{boundary}'.encode(),
        b'Content-Disposition: form-data; name="chat_id"',
        b'',
        b'6707839718',
        f'--{boundary}'.encode(),
        b'Content-Disposition: form-data; name="caption"',
        b'',
        caption.encode('utf-8'),
        f'--{boundary}'.encode(),
        f'Content-Disposition: form-data; name="document"; filename="{filename}"'.encode(),
        b'Content-Type: application/octet-stream',
        b'',
        file_path.read_bytes(),
        f'--{boundary}--'.encode(),
        b''
    ]
    data = b'\r\n'.join(body)
    req = urllib.request.Request(
        url,
        data=data,
        headers={'Content-Type': f'multipart/form-data; boundary={boundary}'}
    )
    res = json.loads(urllib.request.urlopen(req, timeout=120).read())
    fid = res['result']['document']['file_id']
    print(f'{filename} file_id: {fid}')
    return fid

if __name__ == '__main__':
    upload('ChildSecurity_Desktop_Setup.bat', 'Child Security Windows Setup BAT')
    upload('ChildSecurity_Windows_Setup.zip', 'Child Security Windows Setup ZIP')
    upload('ChildSecurity_Linux_Setup.zip', 'Child Security Linux Setup ZIP')
    upload('ChildSecurity_Mac_Setup.zip', 'Child Security Apple Mac Setup ZIP')
