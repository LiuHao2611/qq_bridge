import urllib.request, time, sys
for i in range(40):
    try:
        r = urllib.request.urlopen('http://127.0.0.1:8188/', timeout=3)
        print(f'ComfyUI UP after ~{i*5}s http={r.status}')
        sys.exit(0)
    except Exception:
        pass
    time.sleep(5)
print('ComfyUI NOT UP after poll')
