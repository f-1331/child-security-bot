import urllib.request, json

codes = ['67078397', '31238560', '29909881', '12707685', '21092869', '61105272']

for code in codes:
    req = urllib.request.Request(
        'https://child-security-bot.vercel.app/api/v1/webapp/auth/code',
        data=json.dumps({'code': code}).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'}
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            print(f"Code {code}: 200 OK -> User: {data.get('user', {}).get('full_name')} (ID: {data.get('user', {}).get('id')}) Role: {data.get('role')}")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Code {code}: {e.code} -> {body}")
    except Exception as e:
        print(f"Code {code}: Error -> {e}")
