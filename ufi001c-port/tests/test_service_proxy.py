"""Actual curl HTTPS/CONNECT and isolated APK adapter regressions; no modem writes."""
import copy
import http.server
import json
import os
from pathlib import Path
import select
import shlex
import socket
import socketserver
import ssl
import subprocess
import tempfile
import threading
import unittest

PORT = Path(__file__).resolve().parents[1]
FILES = PORT / 'overlay/package/openstick/ufi-service-proxy/files'
BASE = {
 'inbounds':[{'tag':'service-mixed-in','type':'mixed','listen':'127.0.0.1','listen_port':4534}],
 'outbounds':[{'tag':'p','type':'vless','server':'proxy.example'}],
 'route':{'rules':[{'inbound':['service-mixed-in'],'outbound':'p'}]}}

class QuietServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address=True
    daemon_threads=True
    def handle_error(self, request, address): pass

class ServiceProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(); cls.root=Path(cls.temp.name)
        cls.cert=cls.root/'cert.pem'; key=cls.root/'key.pem'
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-keyout',str(key),'-out',str(cls.cert),'-subj','/CN=ufi-proxy.invalid',
            '-addext','subjectAltName=DNS:ufi-proxy.invalid'],check=True,capture_output=True)
        class Web(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                status=404 if self.path=='/missing' else 200
                self.send_response(status);self.end_headers();self.wfile.write(b'index-body')
            def log_message(self,*a): pass
        cls.web=QuietServer(('127.0.0.1',0),Web)
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(cls.cert,key)
        cls.web.socket=ctx.wrap_socket(cls.web.socket,server_side=True)
        cls.calls=[]
        class Proxy(socketserver.BaseRequestHandler):
            def handle(self):
                data=b''
                self.request.settimeout(5)
                while b'\r\n\r\n' not in data:
                    chunk=self.request.recv(4096)
                    if not chunk:return
                    data+=chunk
                line=data.split(b'\r\n')[0].decode();cls.calls.append(line)
                if not line.startswith('CONNECT ufi-proxy.invalid:'):
                    self.request.sendall(b'HTTP/1.1 403 Forbidden\r\n\r\n');return
                with socket.create_connection(cls.web.server_address,timeout=5) as upstream:
                    self.request.sendall(b'HTTP/1.1 200 Connection established\r\n\r\n')
                    sockets=[self.request,upstream]
                    while True:
                        ready,_,_=select.select(sockets,[],[],5)
                        if not ready:return
                        for src in ready:
                            chunk=src.recv(65536)
                            if not chunk:return
                            (upstream if src is self.request else self.request).sendall(chunk)
        cls.proxy=QuietServer(('127.0.0.1',0),Proxy)
        cls.threads=[]
        for srv in (cls.web,cls.proxy):
            t=threading.Thread(target=srv.serve_forever,daemon=True);t.start();cls.threads.append(t)
    @classmethod
    def tearDownClass(cls):
        for srv in (cls.web,cls.proxy):srv.shutdown();srv.server_close()
        cls.temp.cleanup()
    def setUp(self):
        self.tmpobj=tempfile.TemporaryDirectory();self.addCleanup(self.tmpobj.cleanup)
        self.tmp=Path(self.tmpobj.name);self.bin=self.tmp/'bin';self.bin.mkdir()
        self.config=self.tmp/'config.json';self.config.write_text(json.dumps(BASE))
        self.env=dict(os.environ,PATH=str(self.bin)+os.pathsep+os.environ['PATH'],
                      TEST_CONFIG=str(self.config),CURL_CA_BUNDLE=str(self.cert),
                      NO_PROXY='*',no_proxy='*',TEST_MODE='1',TEST_STOPPED='0')
        self.executable('uci','''#!/bin/sh
case "$*" in *config_path) echo "$TEST_CONFIG";; *ufi_service_proxy) echo "$TEST_MODE";; *) exit 1;; esac
''')
        self.executable('pidof','#!/bin/sh\n[ "$TEST_STOPPED" != 1 ]\n')
        fetch=(FILES/'fetch').read_text().replace('http://127.0.0.1:4534',f'http://127.0.0.1:{self.proxy.server_address[1]}').replace('/usr/share/ufi-service-proxy/ready.jq',str(FILES/'ready.jq'))
        self.fetch=self.executable('fetch',fetch)
        self.wget=self.executable('wget',(FILES/'wget').read_text().replace('/usr/libexec/ufi-service-fetch',str(self.fetch)))
        self.url='https://ufi-proxy.invalid:'+str(self.web.server_address[1])+'/index'
    def executable(self,name,text):
        f=self.bin/name;f.write_text(text);f.chmod(0o755);return f
    def run_wget(self,*args):
        return subprocess.run([str(self.wget),*args],env=self.env,capture_output=True,text=True,timeout=15)
    def test_https_connect_works_when_direct_dns_cannot_and_no_proxy_is_star(self):
        before=len(self.calls);r=self.run_wget('-q','-T','3',self.url,'-O','-')
        self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(r.stdout,'index-body')
        self.assertGreater(len(self.calls),before);self.assertIn('ufi-proxy.invalid',self.calls[-1])
    def test_tls_certificate_validation_stays_enabled(self):
        self.env.pop('CURL_CA_BUNDLE');r=self.run_wget(self.url)
        self.assertNotEqual(r.returncode,0);self.assertIn('certificate',r.stderr.lower())
    def test_stopped_service_fails_before_any_proxy_connection(self):
        self.env['TEST_STOPPED']='1';n=len(self.calls);r=self.run_wget(self.url)
        self.assertEqual(r.returncode,69);self.assertEqual(len(self.calls),n)
        self.assertIn('No direct download',r.stderr)
    def test_invalid_missing_and_public_inbounds_are_rejected(self):
        for data in ({},dict(BASE,inbounds=[]),dict(BASE,inbounds=[dict(BASE['inbounds'][0],listen='0.0.0.0')])):
            self.config.write_text(json.dumps(data));r=self.run_wget(self.url)
            self.assertEqual(r.returncode,69,r.stderr)
        self.config.write_text('{broken');self.assertEqual(self.run_wget(self.url).returncode,69)
    def test_direct_missing_cycles_and_chained_outbounds_are_rejected(self):
        for changes in ([{'tag':'p','type':'direct'}],[],[{'tag':'p','type':'selector','outbounds':['p']}],
                        [dict(BASE['outbounds'][0],detour='q')],BASE['outbounds']*2):
            data=copy.deepcopy(BASE);data['outbounds']=changes;self.config.write_text(json.dumps(data))
            self.assertEqual(self.run_wget(self.url).returncode,69)
    def test_urltest_group_validates_fallbacks(self):
        data=copy.deepcopy(BASE);data['outbounds'] += [dict(BASE['outbounds'][0],tag='q'),
            {'tag':'group','type':'urltest','outbounds':['p'],'fallbacks':['q']}]
        data['route']['rules'][0]['outbound']='group';self.config.write_text(json.dumps(data))
        self.assertEqual(self.run_wget(self.url).returncode,0)
        data['outbounds'][1]['type']='direct';self.config.write_text(json.dumps(data))
        self.assertEqual(self.run_wget(self.url).returncode,69)
    def test_http_error_is_not_a_successful_package(self):
        r=self.run_wget(self.url.replace('/index','/missing'))
        self.assertEqual(r.returncode,22);self.assertEqual(r.stdout,'')
    def test_unsupported_flags_and_insecure_tls_option_are_rejected(self):
        for args in [('--no-check-certificate',self.url),('-T','0',self.url),('-T','bad',self.url),
                     ('--post-data=x',self.url),('-T',),('file:///etc/passwd',)]:
            with self.subTest(args=args):self.assertEqual(self.run_wget(*args).returncode,64)
    def test_native_apk_receives_arguments_and_scoped_path(self):
        target=self.executable('native-apk','#!/bin/sh\nprintf "%s\\n" "$PATH"; printf "%s\\n" "$@"\n')
        wrapper=self.executable('apk',(FILES/'apk').read_text().replace('exec /usr/bin/apk','exec '+str(target)))
        r=subprocess.run([str(wrapper),'add','--simulate','example'],capture_output=True,text=True)
        self.assertEqual(r.returncode,0);lines=r.stdout.splitlines()
        self.assertEqual(lines[0].split(':')[0],'/usr/libexec/ufi-package-bin')
        self.assertEqual(lines[1:],['add','--simulate','example'])
    def download(self,success):
        dest=self.tmp/'list.txt';dest.write_text('old-list')
        helper=PORT/'overlay/package/openstick/podkop/files/usr/lib/helpers.sh'
        stub='printf new-list > "$out"; return 0' if success else 'printf incomplete > "$out"; return 22'
        script=f'''. {shlex.quote(str(helper))}
log() {{ :; }}
sleep() {{ :; }}
curl() {{ while [ "$#" -gt 0 ]; do case "$1" in --output) out=$2; shift;; esac; shift; done; {stub}; }}
download_to_file https://example.invalid/list {shlex.quote(str(dest))} 127.0.0.1:4534 2 0
'''
        r=subprocess.run(['bash','-c',script],capture_output=True,text=True)
        return r,dest
    def test_failed_list_download_preserves_previous_file(self):
        r,d=self.download(False);self.assertNotEqual(r.returncode,0);self.assertEqual(d.read_text(),'old-list')
        self.assertEqual(list(self.tmp.glob('*.download.*')),[])
    def test_successful_list_download_atomically_replaces_file(self):
        r,d=self.download(True);self.assertEqual(r.returncode,0,r.stderr);self.assertEqual(d.read_text(),'new-list')
        self.assertEqual(list(self.tmp.glob('*.download.*')),[])

if __name__=='__main__':unittest.main()
