# -*- coding: utf-8 -*-
"""
Realtime Chat — Kivy (single file runtime + buildozer ready)
- Start a server on one device (Host) and auto-connect as client.
- Other devices press Connect to join host:port.
- Supports text chat (and optional image send as base64, if you select a file).
- Uses 4-byte length-prefixed JSON frames over TCP.
Requirements (buildozer.spec): python3,kivy,pillow
Permissions: INTERNET (and READ_EXTERNAL_STORAGE if you send images)
"""

import os, sys, time, json, socket, struct, threading, base64, tempfile
from kivy.app import App
from kivy.clock import Clock
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.textinput import TextInput
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.recycleview import RecycleView
from kivy.uix.recycleboxlayout import RecycleBoxLayout
from kivy.uix.scrollview import ScrollView
from kivy.uix.popup import Popup
from kivy.uix.filechooser import FileChooserIconView
from kivy.properties import StringProperty, ListProperty
from kivy.metrics import dp

DEFAULT_PORT = 6000
FRAME_PREFIX = 4  # 4-byte length prefix

def send_frame(sock, obj):
    try:
        data = json.dumps(obj, separators=(',',':')).encode('utf-8')
        length = struct.pack('>I', len(data))
        sock.sendall(length + data)
    except Exception:
        pass

def recv_frame(sock):
    data = b''
    while len(data) < 4:
        part = sock.recv(4 - len(data))
        if not part:
            return None
        data += part
    length = struct.unpack('>I', data)[0]
    payload = b''
    while len(payload) < length:
        part = sock.recv(min(4096, length - len(payload)))
        if not part:
            return None
        payload += part
    try:
        return json.loads(payload.decode('utf-8'))
    except Exception:
        return None

class ServerThread(threading.Thread):
    def __init__(self, host='0.0.0.0', port=DEFAULT_PORT):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.sock = None
        self.clients = {}  # conn -> (addr, name)
        self.lock = threading.Lock()

    def broadcast(self, obj, exclude_conn=None):
        with self.lock:
            for conn in list(self.clients.keys()):
                if conn is exclude_conn:
                    continue
                try:
                    send_frame(conn, obj)
                except Exception:
                    try:
                        conn.close()
                    except:
                        pass
                    if conn in self.clients:
                        del self.clients[conn]

    def handle_client(self, conn, addr):
        meta = recv_frame(conn)
        if not meta or meta.get('type') != 'hello' or 'id' not in meta:
            try:
                conn.close()
            except:
                pass
            return
        name = meta.get('name') or meta.get('id')
        with self.lock:
            self.clients[conn] = (addr, name)
        # announce new presence
        self.broadcast({'type':'presence','event':'online','id':meta.get('id'),'name':name})
        # send roster to the newcomer
        with self.lock:
            roster = [{'id': self.clients[c][1], 'addr': self.clients[c][0][0]} for c in self.clients]
        send_frame(conn, {'type':'roster','list': roster})

        while True:
            msg = recv_frame(conn)
            if msg is None:
                break
            mtype = msg.get('type')
            if mtype in ('msg', 'img'):
                target = msg.get('to')
                if target:
                    delivered = False
                    with self.lock:
                        for c,(a,nm) in list(self.clients.items()):
                            if nm == target:
                                send_frame(c, msg)
                                delivered = True
                                break
                else:
                    self.broadcast(msg, exclude_conn=conn)
            elif mtype == 'presence_req':
                with self.lock:
                    send_frame(conn, {'type':'roster','list':[{'id': self.clients[c][1], 'addr': self.clients[c][0][0]} for c in self.clients]})
        # disconnected
        with self.lock:
            name = self.clients.get(conn,[None,None])[1] if conn in self.clients else None
            if conn in self.clients:
                del self.clients[conn]
        self.broadcast({'type':'presence','event':'offline','id':name})
        try:
            conn.close()
        except:
            pass

    def run(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((self.host, self.port))
        s.listen(100)
        self.sock = s
        while True:
            conn, addr = s.accept()
            t = threading.Thread(target=self.handle_client, args=(conn, addr), daemon=True)
            t.start()

class ClientThread(threading.Thread):
    def __init__(self, host, port, myid, myname, on_receive):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.myid = myid
        self.myname = myname
        self.on_receive = on_receive
        self.sock = None
        self.stop_flag = False

    def run(self):
        while not self.stop_flag:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(5.0)
                s.connect((self.host, self.port))
                s.settimeout(None)
                self.sock = s
                send_frame(s, {'type':'hello','id':self.myid,'name':self.myname})
                send_frame(s, {'type':'presence_req'})
                while not self.stop_flag:
                    msg = recv_frame(s)
                    if msg is None:
                        break
                    try:
                        self.on_receive(msg)
                    except Exception:
                        pass
                try:
                    s.close()
                except:
                    pass
            except Exception:
                time.sleep(1.0)
        try:
            if self.sock:
                self.sock.close()
        except:
            pass

    def send_msg(self, obj):
        if not self.sock:
            return False
        try:
            send_frame(self.sock, obj)
            return True
        except Exception:
            return False

    def stop(self):
        self.stop_flag = True
        try:
            if self.sock:
                self.sock.close()
        except:
            pass

class ChatUI(BoxLayout):
    log_text = StringProperty("")
    status_text = StringProperty("Not connected")
    roster_items = ListProperty([])

    def __init__(self, **kwargs):
        super().__init__(orientation='horizontal', **kwargs)
        self.padding = 8
        self.spacing = 8

        # Left column (roster + controls)
        left = BoxLayout(orientation='vertical', size_hint=(0.32, 1))

        # ✅ FIXED ROSTER
        self.roster = RecycleView(size_hint=(1,1))
        self.roster.viewclass = "Label"
        self.roster.add_widget(
            RecycleBoxLayout(
                default_size=(None, dp(48)),
                default_size_hint=(1, None),
                size_hint_y=None,
                orientation="vertical"
            )
        )
        self.roster.data = []

        self.status_lbl = Label(text=self.status_text, size_hint=(1, None), height=28)
        self.id_input = TextInput(hint_text="Your ID (e.g. email/username)", size_hint=(1, None), height=42, multiline=False)
        self.name_input = TextInput(hint_text="Display Name", size_hint=(1, None), height=42, multiline=False)
        self.target_lbl = Label(text="To: (none)", size_hint=(1, None), height=28)

        btn_setid = Button(text="Save ID/Name", size_hint=(1, None), height=44, on_release=lambda *_: self.set_identity())
        btn_host = Button(text="Host (start server)", size_hint=(1, None), height=44, on_release=lambda *_: self.host_dialog())
        btn_connect = Button(text="Connect", size_hint=(1, None), height=44, on_release=lambda *_: self.connect_dialog())

        left.add_widget(Label(text="Contacts / Roster", size_hint=(1, None), height=28))
        left.add_widget(self.roster)
        left.add_widget(self.status_lbl)
        left.add_widget(self.target_lbl)
        left.add_widget(self.id_input)
        left.add_widget(self.name_input)
        left.add_widget(btn_setid)
        left.add_widget(btn_host)
        left.add_widget(btn_connect)

        # Right column (chat)
        right = BoxLayout(orientation='vertical', size_hint=(0.68, 1))
        self.chat_log = Label(text="", halign='left', valign='top', size_hint_y=None)
        self.chat_log.bind(texture_size=self._update_log_height)
        sv = ScrollView(size_hint=(1,1))
        sv.add_widget(self.chat_log)

        row = BoxLayout(orientation='horizontal', size_hint=(1, None), height=50, spacing=6)
        self.input_line = TextInput(hint_text="Type message…", multiline=False)
        btn_send = Button(text="Send", size_hint=(None, 1), width=90, on_release=lambda *_: self.on_send())
        btn_image = Button(text="Send Image", size_hint=(None, 1), width=120, on_release=lambda *_: self.on_send_image())

        row.add_widget(self.input_line); row.add_widget(btn_send); row.add_widget(btn_image)

        right.add_widget(sv)
        right.add_widget(row)

        self.add_widget(left)
        self.add_widget(right)

        # state
        self.myid = None
        self.myname = None
        self.client = None
        self.server_thread = None
        self.selected_target = None

        # click on roster item (simplified)
        self.roster.bind(on_touch_up=self._on_roster_touch)

        # load identity if exists
        self._load_identity()

    def _on_roster_touch(self, rv, touch):
        if not rv.collide_point(*touch.pos):
            return False
        if not rv.data:
            return False
        idx = int((1 - rv.scroll_y) * max(0, len(rv.data)-1))
        if idx < 0 or idx >= len(rv.data):
            idx = 0
        item = rv.data[idx]
        rid = item.get('text')
        if rid:
            self.selected_target = rid
            self.target_lbl.text = f"To: {rid}"
        return False

    def _update_log_height(self, *args):
        self.chat_log.text_size = (self.chat_log.width, None)
        self.chat_log.texture_update()
        self.chat_log.height = max(self.chat_log.texture_size[1] + 20, 400)

    def set_identity(self):
        iid = self.id_input.text.strip()
        nm = (self.name_input.text.strip() or iid)
        if not iid:
            self._toast("Please enter your ID first.")
            return
        self.myid, self.myname = iid, nm
        self._save_identity()
        self.status_lbl.text = f"ID: {self.myid} ({self.myname})"

    def host_dialog(self):
        try:
            port = DEFAULT_PORT
            self.server_thread = ServerThread('0.0.0.0', port)
            self.server_thread.start()
            self._toast(f"Server started on port {port}. Others connect to your IP.")
            self.start_client('127.0.0.1', port)
        except Exception as e:
            self._toast(f"Host error: {e}")

    def connect_dialog(self):
        if not self.myid:
            self._toast("Set ID/Name first.")
            return
        content = BoxLayout(orientation='vertical', spacing=6, padding=6)
        host_in = TextInput(text="127.0.0.1", multiline=False)
        port_in = TextInput(text=str(DEFAULT_PORT), multiline=False)
        btns = BoxLayout(size_hint=(1,None), height=44, spacing=6)
        ok_btn = Button(text="Connect")
        cancel_btn = Button(text="Cancel")
        content.add_widget(Label(text="Host:")); content.add_widget(host_in)
        content.add_widget(Label(text="Port:")); content.add_widget(port_in)
        content.add_widget(btns); btns.add_widget(ok_btn); btns.add_widget(cancel_btn)
        popup = Popup(title="Connect to Host", content=content, size_hint=(0.9,0.6))
        def do_connect(*_):
            try:
                host = host_in.text.strip()
                port = int(port_in.text.strip())
                popup.dismiss()
                self.start_client(host, port)
            except Exception as e:
                self._toast(f"Connect error: {e}")
        ok_btn.bind(on_release=do_connect)
        cancel_btn.bind(on_release=lambda *_: popup.dismiss())
        popup.open()

    def start_client(self, host, port):
        if self.client:
            try: self.client.stop()
            except: pass
            self.client = None
        self.client = ClientThread(host, port, self.myid, self.myname or self.myid, self.on_receive)
        self.client.start()
        self.status_lbl.text = f"Connected to {host}:{port} as {self.myid} ({self.myname})"
        Clock.schedule_once(lambda *_: self.request_roster(), 0.5)

    def request_roster(self):
        if self.client:
            try:
                self.client.send_msg({'type':'presence_req'})
            except:
                pass

    def on_receive(self, msg):
        mtype = msg.get('type')
        if mtype == 'roster':
            lst = msg.get('list') or []
            Clock.schedule_once(lambda *_: self.update_roster(lst), 0)
        elif mtype == 'presence':
            Clock.schedule_once(lambda *_: self.request_roster(), 0)
        elif mtype == 'msg':
            frm = msg.get('from'); body = msg.get('body'); ts = msg.get('ts') or time.time()
            Clock.schedule_once(lambda *_: self.append_message(frm, body, ts, incoming=True), 0)
        elif mtype == 'img':
            frm = msg.get('from'); b64 = msg.get('data'); name = msg.get('name') or 'image'
            try:
                raw = base64.b64decode(b64.encode('utf-8'))
                tmpdir = os.path.join(tempfile.gettempdir(), "realtime_chat_recv")
                os.makedirs(tmpdir, exist_ok=True)
                path = os.path.join(tmpdir, f"recv_{int(time.time()*1000)}_{name}")
                with open(path, 'wb') as f:
                    f.write(raw)
                Clock.schedule_once(lambda *_: self.append_message(frm, f"[Image saved: {path}]", time.time(), incoming=True), 0)
            except Exception:
                pass

    def update_roster(self, lst):
        seen = set()
        data = []
        for r in lst:
            rid = r.get('id') or r.get('name') or r.get('addr')
            if rid and rid not in seen:
                seen.add(rid)
                data.append({'text': rid})
        self.roster.data = data

    def append_message(self, frm, body, ts=None, incoming=False):
        ts = ts or time.time()
        timestr = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        self.chat_log.text += f"[{timestr}] <{frm}>: {body}\n"
        Clock.schedule_once(lambda *_: setattr(self.chat_log, 'text', self.chat_log.text))

    def on_send(self):
        text = self.input_line.text.strip()
        if not text:
            return
        to = self.selected_target
        msg = {'type':'msg','from':self.myid,'to': to, 'body': text, 'ts': time.time()}
        self.append_message(self.myid, text, ts=msg['ts'], incoming=False)
        if self.client:
            self.client.send_msg(msg)
        self.input_line.text = ""

    def on_send_image(self):
        if not self.client:
            self._toast("Connect to a host first.")
            return
        chooser = FileChooserIconView(filters=['*.png','*.jpg','*.jpeg','*.bmp'])
        ok_btn = Button(text="Send", size_hint=(1, None), height=48)
        box = BoxLayout(orientation='vertical')
        box.add_widget(chooser); box.add_widget(ok_btn)
        popup = Popup(title="Select image", content=box, size_hint=(0.95,0.95))

        def do_send(*_):
            if not chooser.selection:
                self._toast("No file selected.")
                return
            fname = chooser.selection[0]
            try:
                with open(fname, 'rb') as f:
                    data = base64.b64encode(f.read()).decode('utf-8')
                to = self.selected_target
                obj = {'type':'img','from':self.myid,'to': to, 'data': data, 'name': os.path.basename(fname), 'ts': time.time()}
                self.append_message(self.myid, f"[Image sent: {os.path.basename(fname)}]", time.time(), incoming=False)
                self.client.send_msg(obj)
                popup.dismiss()
            except Exception as e:
                self._toast(f"Send error: {e}")
        ok_btn.bind(on_release=do_send)
        popup.open()

    def _toast(self, txt):
        pop = Popup(title="Info", content=Label(text=txt), size_hint=(0.8, 0.3))
        Clock.schedule_once(lambda *_: pop.dismiss(), 2.0)
        pop.open()

    def _load_identity(self):
        try:
            p = os.path.join(self._app_dir(), "chat_identity.json")
            if os.path.exists(p):
                d = json.load(open(p, 'r', encoding='utf-8'))
                self.myid = d.get('id'); self.myname = d.get('name')
                self.id_input.text = self.myid or ""
                self.name_input.text = self.myname or (self.myid or "")
                if self.myid:
                    self.status_lbl.text = f"ID: {self.myid} ({self.myname})"
        except Exception:
            pass
    def _save_identity(self):
        try:
            p = os.path.join(self._app_dir(), "chat_identity.json")
            d = {'id': self.myid, 'name': self.myname}
            json.dump(d, open(p, 'w', encoding='utf-8'), indent=2)
        except Exception:
            pass
    def _app_dir(self):
        if hasattr(sys, '_MEIPASS'):
            return sys._MEIPASS
        return os.path.dirname(os.path.abspath(__file__))
    def on_stop(self):
        if self.client:
            try: self.client.stop()
            except: pass
        if self.server_thread:
            try:
                # no direct stop, just close server socket
                if self.server_thread.sock:
                    self.server_thread.sock.close()
            except: pass
        self.client = None