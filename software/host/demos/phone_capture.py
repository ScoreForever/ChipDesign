"""User-started phone DeviceMotion capture to an append-only, new CSV file.

LAN browsers generally require HTTPS (--cert/--key); Android USB port reverse
can use localhost. No sensor is accessed until the participant presses Start.
"""

import argparse
import csv
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import secrets
import ssl
import threading
from urllib.parse import parse_qs, urlsplit


PAGE = """<!doctype html><html lang="zh"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ChipDesign 手机 IMU</title><style>
body{font:18px system-ui;background:#141a21;color:#eef4f6;margin:24px;max-width:650px}
button{padding:16px;margin:8px 10px 8px 0;font-size:18px;background:#90ecd2;border:0;border-radius:8px}
p{line-height:1.7}#status{white-space:pre-wrap;color:#90ecd2}</style>
<h1>人体活动 · 手机采集</h1><p>将手机固定在腰部并保持方向不变。采集加速度和角速度，传给本机 Demo；不采集位置、声音或图像。</p>
<p>轴方向需按现场佩戴校准。结果是工程演示，尚未完成参与者验证。</p>
<button id="start">开始采集</button><button id="stop" disabled>停止</button><p id="status">尚未采集</p>
<script>
const token=new URLSearchParams(location.search).get('token');
let active=false,buffer=[],count=0,timer,chain=Promise.resolve();
const status=document.querySelector('#status');
function end(){active=false;clearInterval(timer);removeEventListener('devicemotion',sample);document.querySelector('#stop').disabled=true;document.querySelector('#start').disabled=false}
function flush(){if(!buffer.length)return;let batch=buffer;buffer=[];chain=chain.then(async()=>{let r=await fetch('/samples',{method:'POST',headers:{'Content-Type':'application/json','X-Demo-Token':token},body:JSON.stringify(batch)});if(!r.ok)throw Error(await r.text());count+=batch.length;status.textContent=`已保存 ${count} 点\n将电脑端 HAR 页面设为跟随这个 CSV。`}).catch(e=>{end();status.textContent='采集停止：'+e.message;throw e});chain.catch(()=>{})}
function sample(e){if(!active)return;const a=e.accelerationIncludingGravity,g=e.rotationRate;if(!a||!g||[a.x,a.y,a.z,g.beta,g.gamma,g.alpha].some(v=>v==null||!Number.isFinite(v))){end();status.textContent='设备未提供完整的加速度和角速度';return}buffer.push([e.timeStamp/1000,a.x,a.y,a.z,g.beta*Math.PI/180,g.gamma*Math.PI/180,g.alpha*Math.PI/180]);if(buffer.length>300){end();status.textContent='采集过载，请重新开始';buffer=[]}}
document.querySelector('#start').onclick=async()=>{try{if(!isSecureContext)throw Error('请使用 HTTPS 或 USB localhost 连接');if(typeof DeviceMotionEvent==='undefined')throw Error('此浏览器没有 DeviceMotion');if(DeviceMotionEvent.requestPermission&&await DeviceMotionEvent.requestPermission()!=='granted')throw Error('没有获得传感器权限');buffer=[];chain=Promise.resolve();active=true;count=0;document.querySelector('#start').disabled=true;document.querySelector('#stop').disabled=false;addEventListener('devicemotion',sample);timer=setInterval(flush,500);status.textContent='采集中；至少保持 3 秒';}catch(e){status.textContent=e.message}};
document.querySelector('#stop').onclick=()=>{end();flush();chain.then(()=>status.textContent+= '\\n已停止').catch(()=>{})};
</script></html>"""


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self,address,output,token):
        self.token=token
        self.lock=threading.Lock()
        self.last_time=-math.inf
        self.count=0
        self.output=Path(output)
        self.output.parent.mkdir(parents=True,exist_ok=True)
        super().__init__(address,Handler)
        try:
            self.handle=self.output.open('x',encoding='utf-8',newline='')
        except Exception:
            self.server_close()
            raise
        self.writer=csv.writer(self.handle)
        self.writer.writerow(('time_s','ax','ay','az','gx','gy','gz'))
        self.handle.flush()

    def server_close(self):
        super().server_close()
        if hasattr(self,'handle'):
            self.handle.close()


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_):
        pass  # Do not log the access token from URLs.

    def respond(self,code,text):
        payload=text.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed=urlsplit(self.path)
        if parsed.path!='/' or not secrets.compare_digest(parse_qs(parsed.query).get('token',[''])[0],self.server.token):
            self.respond(403,'Invalid capture token')
            return
        self.respond(200,PAGE)

    def do_POST(self):
        if urlsplit(self.path).path!='/samples' or not secrets.compare_digest(self.headers.get('X-Demo-Token',''),self.server.token):
            self.respond(403,'Invalid capture token')
            return
        origin=self.headers.get('Origin')
        if origin and urlsplit(origin).netloc != self.headers.get('Host'):
            self.respond(403,'Same-origin requests only')
            return
        try:
            self.connection.settimeout(5)
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=200000:
                raise ValueError('Invalid packet length')
            rows=json.loads(self.rfile.read(length))
            if not isinstance(rows,list) or not 1<=len(rows)<=300:
                raise ValueError('Expected 1..300 samples')
            for row in rows:
                if not isinstance(row,list) or len(row)!=7 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in row):
                    raise ValueError('Expected seven finite numbers per sample')
                if row[0]<0 or any(abs(v)>1000 for v in row[1:4]) or any(abs(v)>100 for v in row[4:]):
                    raise ValueError('Invalid timestamp or sensor units')
            with self.server.lock:
                times=[self.server.last_time]+[r[0] for r in rows]
                if any(b<=a for a,b in zip(times,times[1:])):
                    raise ValueError('Timestamps must increase; start a new CSV for a new capture session')
                self.server.writer.writerows(rows)
                self.server.handle.flush()
                self.server.last_time=rows[-1][0]
                self.server.count+=len(rows)
            self.respond(200,'ok')
        except (OSError,ValueError,TypeError) as exc:
            self.respond(400,str(exc))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bind',default='127.0.0.1')
    p.add_argument('--port',type=int,default=8765)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--cert',type=Path)
    p.add_argument('--key',type=Path)
    args=p.parse_args()
    if bool(args.cert)!=bool(args.key):
        p.error('--cert and --key must be supplied together')
    token=secrets.token_urlsafe(24)
    server=CaptureServer((args.bind,args.port),args.output,token)
    try:
        if args.cert:
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(args.cert,args.key)
            server.socket=context.wrap_socket(server.socket,server_side=True)
        print(f"{'https' if args.cert else 'http'}://{args.bind}:{server.server_port}/?token={token}",flush=True)
        print(f'CSV: {args.output.resolve()} (s, m/s^2, rad/s); Ctrl+C stops capture',flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__=='__main__':
    main()
