import urllib.request
import urllib.error

req = urllib.request.Request('http://127.0.0.1:8000/api/invoices/4aef3ab8-58aa-4836-a31c-e110a2d73eaf/process', method='POST')
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print(e.read().decode())
