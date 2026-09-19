from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, quote, parse_qs
import json, re, html, tarfile, io, time, os, urllib.request, urllib.error, hashlib, hmac, secrets, threading, tempfile

try:
    import psycopg2
    from psycopg2.extras import Json as PostgresJson
except ImportError:  # Optional dependency: beta JSON storage remains available.
    psycopg2 = None
    PostgresJson = None

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / 'generated'
DATA.mkdir(exist_ok=True)
STORE_FILE = DATA / 'stores.json'
AUTH_FILE = DATA / 'auth.json'
ORDER_FILE = DATA / 'orders.json'
CHAT_FILE = DATA / 'conversations.json'
PAYMENT_HISTORY_FILE = DATA / 'payment_history.json'
AUDIT_FILE = DATA / 'audit.json'
ACCESS_CODE_FILE = DATA / 'access_codes.json'
BACKUP_DIR = DATA / 'backups'
BACKUP_DIR.mkdir(exist_ok=True)
SESSION_TTL = 30 * 86400
MAX_BODY_BYTES = 2 * 1024 * 1024
DATA_LOCK = threading.RLock()
AGENT_FILE = DATA / 'agent_settings.json'
CUSTOMER_FILE = DATA / 'customers.json'
VIDEO_FILE = DATA / 'videos.json'
MAX_VIDEO_BYTES = 320 * 1024
MAX_VIDEO_DATA_URL = 460000
PUBLIC_BASE = 'https://forgeai-app-builder.onrender.com'
PAYMENT_FILE = DATA / 'payment_settings.json'
DEFAULT_AGENT_SETTINGS = {'min_margin':20,'max_discount':10,'require_approval':True,'tone':'consultivo','persona_name':'Consultora Certa','persona_description':'Uma vendedora atenciosa, clara e honesta da sua loja.','pilot_mode':True,'negotiation_mode':'suggestion'}
PLATFORM_COMMISSION_RATE = 0.05
PLAN_DAYS = {'BASICO':30,'PRO':180,'ENTERPRISE':365}
PLAN_PRICES = {'BASICO':98.90,'PRO':489.90,'ENTERPRISE':1089.90}
PLAN_LABELS = {'BASICO':'Básico','PRO':'Pro','ENTERPRISE':'Enterprise'}
DEFAULT_PRODUCTS = [{'name':'Produto especial','desc':'Qualidade e estilo para você.','price':'R$ 49,90'},{'name':'Mais vendido','desc':'O favorito dos clientes.','price':'R$ 79,90'},{'name':'Novidade','desc':'Acabou de chegar na loja.','price':'R$ 99,90'}]


class StorageBackend:
    """Small storage port shared by the current domain functions.

    PostgreSQL is deliberately a key/value envelope for this migration phase: it
    preserves the existing JSON-shaped domain data while giving Render a durable,
    transactional store. The beta filesystem path is explicit and never reported
    as durable production storage.
    """
    VERSION = 'kv-v1'

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.url_configured = bool(os.environ.get('DATABASE_URL', '').strip())
        self.mode = 'json-beta'
        self.reason = 'DATABASE_URL não configurada; armazenamento local não durável para beta.'
        self.migration = 'not_configured'
        self.lock = threading.RLock()
        if self.url_configured and psycopg2 is None:
            self.reason = 'DATABASE_URL configurada, mas o driver PostgreSQL não está instalado.'
        elif self.url_configured:
            try:
                self._ensure_schema()
                self._migrate_legacy_files()
                self.mode = 'postgresql'
                self.reason = ''
                self.migration = 'kv-v1-ready'
            except Exception as exc:
                self.mode = 'json-beta'
                self.reason = 'PostgreSQL configurado, mas indisponível; fallback local beta ativo (' + type(exc).__name__ + ').'
                self.migration = 'failed'

    def _connect(self):
        return psycopg2.connect(self.url_configured and os.environ.get('DATABASE_URL', '').strip(), connect_timeout=8)

    def _ensure_schema(self):
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS vendacertaai_kv (
                    key TEXT PRIMARY KEY,
                    value JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )''')
                cur.execute('''CREATE TABLE IF NOT EXISTS vendacertaai_migrations (
                    version TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )''')
                cur.execute("INSERT INTO vendacertaai_migrations(version) VALUES (%s) ON CONFLICT (version) DO NOTHING", (self.VERSION,))
            conn.commit()

    def _migrate_legacy_files(self):
        files = list(self.data_dir.glob('*.json'))
        with self._connect() as conn:
            with conn.cursor() as cur:
                for path in files:
                    try:
                        value = json.loads(path.read_text(encoding='utf-8'))
                    except Exception:
                        continue
                    cur.execute('''INSERT INTO vendacertaai_kv(key,value) VALUES (%s,%s)
                                   ON CONFLICT (key) DO NOTHING''', (path.name, PostgresJson(value)))
            conn.commit()

    def _fallback_read(self, path, default):
        try:
            if path.exists():
                return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            pass
        return default

    def read(self, path, default):
        if self.mode != 'postgresql':
            return self._fallback_read(path, default)
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('SELECT value FROM vendacertaai_kv WHERE key=%s', (path.name,))
                    row = cur.fetchone()
                    return row[0] if row else default
        except Exception as exc:
            self.mode = 'json-beta'
            self.reason = 'Falha de leitura PostgreSQL; fallback local beta ativo (' + type(exc).__name__ + ').'
            return self._fallback_read(path, default)

    def write(self, path, value):
        if self.mode != 'postgresql':
            return False
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute('''INSERT INTO vendacertaai_kv(key,value,updated_at) VALUES (%s,%s,NOW())
                                   ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value, updated_at=NOW()''', (path.name, PostgresJson(value)))
                conn.commit()
            return True
        except Exception as exc:
            self.mode = 'json-beta'
            self.reason = 'Falha de gravação PostgreSQL; fallback local beta ativo (' + type(exc).__name__ + ').'
            return False

    def status(self):
        durable = self.mode == 'postgresql'
        return {
            'backend': 'postgresql' if durable else 'json-beta',
            'durable': durable,
            'database_url_configured': self.url_configured,
            'migration': self.migration,
            'version': self.VERSION,
            'fallback': not durable,
            'message': self.reason if not durable else 'PostgreSQL durável ativo.'
        }


# Initialized once; all later data access goes through this port.
STORAGE = StorageBackend(DATA)


def storage_status():
    return STORAGE.status()


def load_json(path, default):
    value = STORAGE.read(path, default)
    if value is not default:
        return value
    # Recover from the most recent local backup after a torn beta write.
    try:
        candidates=sorted(BACKUP_DIR.glob(path.name+'.*.bak'), reverse=True)
        if candidates:
            return json.loads(candidates[0].read_text(encoding='utf-8'))
    except Exception:
        pass
    return default


def atomic_save(path, value):
    raw=json.dumps(value, ensure_ascii=False, indent=2)
    if STORAGE.write(path, value):
        return
    with DATA_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            try:
                backup=BACKUP_DIR / (path.name+'.'+str(int(time.time()*1000))+'.bak')
                backup.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
                old=sorted(BACKUP_DIR.glob(path.name+'.*.bak'), reverse=True)
                for stale in old[8:]:
                    try: stale.unlink()
                    except OSError: pass
            except Exception:
                pass
        fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.', dir=str(path.parent))
        try:
            with os.fdopen(fd,'w',encoding='utf-8') as fh:
                fh.write(raw); fh.flush(); os.fsync(fh.fileno())
            os.replace(tmp,path)
        finally:
            if os.path.exists(tmp):
                try: os.unlink(tmp)
                except OSError: pass


def audit_event(slug, event, details=None):
    with DATA_LOCK:
        data=load_json(AUDIT_FILE,{})
        if not isinstance(data,dict): data={}
        events=data.setdefault(str(slug),[])
        events.append({'id':secrets.token_hex(10),'event':str(event)[:80],'details':details if isinstance(details,dict) else {},'at':time.time()})
        data[str(slug)]=events[-500:]
        atomic_save(AUDIT_FILE,data)


def auth_data():
    data=load_json(AUTH_FILE,{})
    if not isinstance(data,dict): data={}
    users=data.get('users',{}) if isinstance(data.get('users',{}),dict) else {}
    sessions=data.get('sessions',{}) if isinstance(data.get('sessions',{}),dict) else {}
    admin_sessions=data.get('admin_sessions',{}) if isinstance(data.get('admin_sessions',{}),dict) else {}
    return {'users':users,'sessions':sessions,'admin_sessions':admin_sessions}


def save_auth(data):
    atomic_save(AUTH_FILE,data)


def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt.encode('utf-8'),120000).hex()
    return salt, digest


def session_user(handler):
    token=''
    for part in handler.headers.get('Cookie','').split(';'):
        if part.strip().startswith('vc_session='): token=part.strip().split('=',1)[1]
    data=auth_data(); entry=data['sessions'].get(token)
    if isinstance(entry,str):
        # Migrate legacy sessions lazily; old sessions expire after this request.
        email=entry; entry={'email':email,'created_at':time.time()}
        data['sessions'][token]=entry; save_auth(data)
    if not isinstance(entry,dict): return None
    if time.time()-float(entry.get('created_at',0) or 0)>SESSION_TTL:
        data['sessions'].pop(token,None); save_auth(data); return None
    email=str(entry.get('email','')).lower()
    return data['users'].get(email) if email else None


def set_session(handler, token, admin=False):
    name='vc_admin' if admin else 'vc_session'
    handler.send_header('Set-Cookie',f'{name}={token}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={SESSION_TTL}')


def store_products(slug='vendacertaai'):
    """Return a normalized tenant catalog; migrate legacy entries lacking IDs."""
    data=load_json(STORE_FILE,{})
    products=data.get(str(slug)) if isinstance(data,dict) else None
    if not isinstance(products,list) or not products:
        products=DEFAULT_PRODUCTS.copy()
    normalized=[]; changed=False
    for raw in products:
        item=clean_product(raw) if isinstance(raw,dict) else None
        if item:
            normalized.append(item)
            if item != raw: changed=True
    if not normalized:
        normalized=[clean_product(p) for p in DEFAULT_PRODUCTS]
        changed=True
    if changed:
        save_store_products(slug, normalized)
    return normalized


def save_store_products(slug, products):
    with DATA_LOCK:
        data=load_json(STORE_FILE,{})
        if not isinstance(data,dict): data={}
        data[str(slug)]=products
        atomic_save(STORE_FILE,data)


def store_orders(slug='vendacertaai'):
    data=load_json(ORDER_FILE,{})
    orders=data.get(str(slug),[]) if isinstance(data,dict) else []
    return orders if isinstance(orders,list) else []


def save_store_orders(slug, orders):
    with DATA_LOCK:
        data=load_json(ORDER_FILE,{})
        if not isinstance(data,dict): data={}
        data[str(slug)]=orders
        atomic_save(ORDER_FILE,data)


def conversation_history(slug, session_id):
    data=load_json(CHAT_FILE,{})
    history=data.get(str(slug),{}).get(str(session_id),[]) if isinstance(data,dict) and isinstance(data.get(str(slug),{}),dict) else []
    return history if isinstance(history,list) else []


def save_conversation(slug, session_id, history):
    with DATA_LOCK:
        data=load_json(CHAT_FILE,{})
        if not isinstance(data,dict): data={}
        data.setdefault(str(slug),{})[str(session_id)]=history[-20:]
        atomic_save(CHAT_FILE,data)


def agent_settings(slug='vendacertaai'):
    data=load_json(AGENT_FILE,{})
    current=data.get(str(slug),{}) if isinstance(data,dict) else {}
    return {**DEFAULT_AGENT_SETTINGS,**current} if isinstance(current,dict) else DEFAULT_AGENT_SETTINGS.copy()


def save_agent_settings(slug, settings):
    with DATA_LOCK:
        data=load_json(AGENT_FILE,{})
        if not isinstance(data,dict): data={}
        data[str(slug)]=settings
        atomic_save(AGENT_FILE,data)


def customer_profile(slug, session_id):
    data=load_json(CUSTOMER_FILE,{})
    profile=data.get(str(slug),{}).get(str(session_id),{}) if isinstance(data,dict) and isinstance(data.get(str(slug),{}),dict) else {}
    return profile if isinstance(profile,dict) else {}


def save_customer_profile(slug, session_id, profile):
    with DATA_LOCK:
        data=load_json(CUSTOMER_FILE,{})
        if not isinstance(data,dict): data={}
        data.setdefault(str(slug),{})[str(session_id)]=profile
        atomic_save(CUSTOMER_FILE,data)


def store_videos(slug='vendacertaai'):
    data=load_json(VIDEO_FILE,{})
    items=data.get(str(slug),[]) if isinstance(data,dict) else []
    return items if isinstance(items,list) else []


def save_store_videos(slug, videos):
    with DATA_LOCK:
        data=load_json(VIDEO_FILE,{})
        if not isinstance(data,dict): data={}
        data[str(slug)]=videos[-100:]
        atomic_save(VIDEO_FILE,data)


def clean_video_payload(raw, slug):
    if not isinstance(raw,dict): return None, 'Vídeo inválido.'
    caption=str(raw.get('caption','')).strip()[:500]
    product_id=str(raw.get('product_id','')).strip()[:40]
    product=next((p for p in store_products(slug) if str(p.get('id',''))==product_id),None)
    if not product: return None, 'Selecione um produto válido do catálogo.'
    video=str(raw.get('video','')).strip()
    if not re.fullmatch(r'data:video/(?:mp4|webm|quicktime);base64,[A-Za-z0-9+/=]+',video,re.I):
        return None, 'Envie um vídeo MP4, WebM ou MOV válido.'
    if len(video)>MAX_VIDEO_DATA_URL: return None, 'O vídeo precisa ter até 320 KB após a compressão.'
    try:
        encoded=video.split(',',1)[1]
        if len(__import__('base64').b64decode(encoded, validate=True))>MAX_VIDEO_BYTES: return None, 'O vídeo precisa ter até 320 KB.'
    except Exception: return None, 'Arquivo de vídeo inválido.'
    poster=str(raw.get('poster','')).strip()
    if poster:
        if not re.fullmatch(r'data:image/(?:jpeg|jpg|png|webp);base64,[A-Za-z0-9+/=]+',poster,re.I) or len(poster)>90000:
            poster=''
    return {'id':str(raw.get('id') or secrets.token_hex(10))[:40],'product_id':product['id'],'product_name':product['name'],'price':product['price'],'caption':caption,'video':video,'poster':poster,'created_at':time.time()}, None


def payment_history():
    data=load_json(PAYMENT_HISTORY_FILE,{})
    return data if isinstance(data,dict) else {}


def save_payment_record(record):
    payment_key=str(record.get('payment_id') or record.get('preference_id') or record.get('reference') or '')
    if not payment_key: return
    with DATA_LOCK:
        data=payment_history(); data[payment_key]=record; atomic_save(PAYMENT_HISTORY_FILE,data)


def find_payment_record(payment_id='', reference=''):
    data=payment_history()
    for record in data.values():
        if not isinstance(record,dict): continue
        if payment_id and str(record.get('payment_id',''))==str(payment_id): return record
        if reference and str(record.get('reference',''))==str(reference): return record
    return None


def plan_payment_valid(payment, plan):
    try:
        return str(payment.get('currency_id','BRL')).upper()=='BRL' and abs(float(payment.get('transaction_amount',0))-PLAN_PRICES[plan])<0.01
    except Exception:
        return False


def clean_product(product):
    if not isinstance(product,dict): return None
    name=str(product.get('name','')).strip()[:120]
    desc=str(product.get('desc',product.get('description',''))).strip()[:1000]
    price=price_number(product.get('price',''))
    if not name or price<=0: return None
    result={'id':str(product.get('id') or secrets.token_hex(8))[:40],'name':name,'desc':desc or 'Produto disponível na loja.','price':f'R$ {price:.2f}'.replace('.',',')}
    # Legacy image remains supported; images is the bounded multi-image gallery.
    gallery=[]
    raw_images=product.get('images',[]) if isinstance(product.get('images',[]),list) else []
    candidates=raw_images[:6]
    if product.get('image') and not candidates: candidates=[product.get('image')]
    for candidate in candidates:
        image=str(candidate or '').strip()
        if image.startswith('data:image/'):
            match=re.fullmatch(r'data:image/(?:jpeg|jpg|png|webp|gif);base64,[A-Za-z0-9+/=]+',image,re.I)
            if match and len(image)<=240000: gallery.append(image)
        elif re.fullmatch(r'https://[^\s<>"\']{1,480}',image,re.I): gallery.append(image)
    if gallery:
        result['images']=gallery
        result['image']=gallery[0]
    for key in ('category','image','stock'):
        if key in product:
            if key=='images': continue
            if key=='stock':
                try: result[key]=max(0,min(100000,int(product[key])))
                except Exception: result[key]=0
            elif key=='image':
                image=str(product.get('image','')).strip()
                # Images are client-compressed data URLs or existing HTTPS URLs only.
                # Keep this bounded so JSON/JSONB catalogs cannot be used as a blob store.
                if image.startswith('data:image/'):
                    match=re.fullmatch(r'data:image/(?:jpeg|jpg|png|webp|gif);base64,[A-Za-z0-9+/=]+',image,re.I)
                    if match and len(image)<=240000: result['image']=image
                elif re.fullmatch(r'https://[^\s<>"\']{1,480}',image,re.I):
                    result['image']=image
            else: result[key]=str(product[key]).strip()[:500]
    return result


def catalog_product(slug, item):
    products=store_products(slug)
    item_id=str(item.get('product_id','')).strip() if isinstance(item,dict) else ''
    name=str(item.get('product','')).strip() if isinstance(item,dict) else ''
    for product in products:
        if item_id and str(product.get('id',''))==item_id: return product
        if name and str(product.get('name','')).strip().casefold()==name.casefold(): return product
    return None

def payment_token(slug):
    # Access tokens must stay in the platform secret manager/environment, never tenant JSON.
    return ''


def save_payment_token(slug, token):
    return False


def price_number(value):
    raw=re.sub(r'[^0-9,.-]','',str(value or '')).replace('.','').replace(',','.')
    try: return round(float(raw),2)
    except Exception: return 0.0


def details_token_hash(token):
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


def public_order(order):
    # Never return the capability token/hash to browsers or tenant dashboards.
    return {k:v for k,v in order.items() if k != 'details_token_hash'}


def clean_address(body):
    if not isinstance(body, dict): return None, 'Dados de endereço inválidos.'
    fields={k:str(body.get(k,'')).strip()[:120] for k in ('address','number','complement','neighborhood','city','state','zip')}
    if not fields['address'] or not fields['number'] or not fields['city'] or not fields['state'] or not fields['zip']:
        return None, 'Informe rua, número, cidade, estado e CEP.'
    if not re.fullmatch(r'[0-9A-Za-zÀ-ÿ .\-]{5,12}', fields['zip']):
        return None, 'CEP inválido.'
    if len(fields['state']) not in (2,) and not (3 <= len(fields['state']) <= 40):
        return None, 'Estado inválido.'
    return fields, None


def create_mp_preference(title, price, order_id, slug, details_token=''):
    key=os.environ.get('MERCADO_PAGO_ACCESS_TOKEN','').strip() or payment_token(slug)
    if not key: return None, 'Conecte o Mercado Pago nas configurações da loja antes de pagar.'
    payload={'items':[{'title':str(title)[:120],'quantity':1,'unit_price':price,'currency_id':'BRL'}],'external_reference':f'{slug}:{order_id}','back_urls':{'success':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=success&order_id='+quote(str(order_id))+'&details_token='+quote(str(details_token)),'pending':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=pending&order_id='+quote(str(order_id))+'&details_token='+quote(str(details_token)),'failure':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=failure&order_id='+quote(str(order_id))+'&details_token='+quote(str(details_token))},'auto_return':'approved','notification_url':PUBLIC_BASE+'/api/payment/webhook'}
    try:
        req=urllib.request.Request('https://api.mercadopago.com/checkout/preferences',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
        with urllib.request.urlopen(req,timeout=30) as response: data=json.loads(response.read().decode('utf-8'))
        return data,None
    except urllib.error.HTTPError as e:
        return None,'Não foi possível criar o checkout no Mercado Pago.'
    except Exception:
        return None,'Mercado Pago indisponível no momento.'


def create_plan_preference(email, plan):
    key=os.environ.get('MERCADO_PAGO_ACCESS_TOKEN','').strip()
    amount=PLAN_PRICES.get(plan,0)
    if not key or amount<=0: return None, 'Checkout indisponível no momento.'
    reference='PLAN|'+email+'|'+plan+'|'+secrets.token_hex(8)
    payload={'items':[{'title':'VendaCertaAI '+PLAN_LABELS[plan]+' - '+str(PLAN_DAYS[plan])+' dias','quantity':1,'unit_price':amount,'currency_id':'BRL'}],'external_reference':reference,'back_urls':{'success':PUBLIC_BASE+'/vendacertaai?plan=success','pending':PUBLIC_BASE+'/vendacertaai?plan=pending','failure':PUBLIC_BASE+'/vendacertaai?plan=failure'},'auto_return':'approved','notification_url':PUBLIC_BASE+'/api/payment/webhook'}
    try:
        req=urllib.request.Request('https://api.mercadopago.com/checkout/preferences',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
        with urllib.request.urlopen(req,timeout=30) as response: return json.loads(response.read().decode('utf-8')),None
    except urllib.error.HTTPError:
        return None,'Não foi possível criar o checkout no Mercado Pago.'
    except Exception:
        return None,'Mercado Pago indisponível no momento.'


def mp_payment(payment_id):
    key=os.environ.get('MERCADO_PAGO_ACCESS_TOKEN','').strip()
    if not key or not str(payment_id).strip(): return None
    try:
        req=urllib.request.Request('https://api.mercadopago.com/v1/payments/'+quote(str(payment_id)),headers={'Authorization':'Bearer '+key},method='GET')
        with urllib.request.urlopen(req,timeout=20) as response: return json.loads(response.read().decode('utf-8'))
    except Exception:
        return None


def payment_label(status):
    return {'approved':'Pago','pending':'Pagamento pendente','in_process':'Pagamento em análise','rejected':'Pagamento recusado','cancelled':'Pagamento cancelado','refunded':'Pagamento estornado','charged_back':'Pagamento contestado'}.get(str(status or ''),'Pagamento pendente')


def apply_payment_update(payment):
    if not isinstance(payment,dict): return False
    reference=str(payment.get('external_reference','')).strip(); status=str(payment.get('status','')).lower()
    payment_id=str(payment.get('id','')).strip()
    if reference.startswith('PLAN|'):
        parts=reference.split('|')
        email=parts[1].lower() if len(parts)>1 else ''
        plan=parts[2].upper() if len(parts)>2 else ''
        if not email or plan not in PLAN_DAYS or not payment_id: return False
        record=find_payment_record(payment_id,reference)
        if not plan_payment_valid(payment,plan):
            if record:
                record.update({'status':status,'updated_at':time.time(),'validation':'amount_or_currency_mismatch'}); save_payment_record(record)
            return False
        record=record or {'reference':reference,'email':email,'plan':plan,'amount':PLAN_PRICES[plan],'days':PLAN_DAYS[plan],'created_at':time.time()}
        record.update({'payment_id':payment_id,'status':status,'payment_status':status,'updated_at':time.time()})
        if status!='approved':
            save_payment_record(record); return False
        data=auth_data(); user=data['users'].get(email)
        if not user: record['validation']='account_not_found'; save_payment_record(record); return False
        if str(user.get('last_payment_id',''))==payment_id or record.get('activated_at'):
            save_payment_record(record); return True
        now=time.time(); start=max(now,float(user.get('access_until',0) or 0)); user['access_plan']=plan; user['access_until']=start+PLAN_DAYS[plan]*86400; user['plan_status']='active'; user['last_payment_id']=payment_id; user['last_payment_at']=now; user['payment_history_count']=int(user.get('payment_history_count',0) or 0)+1
        save_auth(data); record['activated_at']=now; record['activation_until']=user['access_until']; save_payment_record(record); audit_event(user.get('slug',''), 'plan_activated', {'plan':plan,'payment_id':payment_id,'days':PLAN_DAYS[plan]}); return True
    if ':' not in reference: return False
    slug,order_id=reference.split(':',1); orders=store_orders(slug); changed=False
    for order in orders:
        if str(order.get('id'))==order_id:
            order['status']=payment_label(status); order['payment_id']=payment_id; order['payment_status']=status; order['updated_at']=time.time(); changed=True
    if changed: save_store_orders(slug,orders); audit_event(slug,'order_payment_updated',{'order_id':order_id,'payment_id':payment_id,'status':status})
    return changed


def access_status(user):
    now=time.time(); trial_until=float(user.get('trial_ends_at',0) or 0); access_until=float(user.get('access_until',0) or 0); active_trial=trial_until>now; active_plan=access_until>now
    return {'trial_active':active_trial,'trial_ends_at':trial_until,'plan_active':active_plan,'plan':user.get('access_plan'),'plan_status':user.get('plan_status'),'access_until':access_until,'payment_history_count':int(user.get('payment_history_count',0) or 0),'last_payment_at':user.get('last_payment_at'),'locked':not(active_trial or active_plan)}


def user_by_slug(slug):
    data=auth_data()
    return next((u for u in data['users'].values() if u.get('slug')==slug),None)


def store_access_active(slug):
    slug=str(slug).strip()
    user=user_by_slug(slug)
    # The built-in demo store is public; every other tenant must exist and be active.
    return True if slug=='vendacertaai' and not user else bool(user and not access_status(user)['locked'])


def verify_mp_webhook(handler, body):
    secret=os.environ.get('MERCADO_PAGO_WEBHOOK_SECRET','').strip()
    if not secret: return True  # Signature verification becomes mandatory as soon as MP secret is configured.
    signature=handler.headers.get('x-signature',''); request_id=handler.headers.get('x-request-id','')
    query=parse_qs(urlparse(handler.path).query)
    data_id=(query.get('data.id') or query.get('id') or [''])[0]
    if not data_id and isinstance(body.get('data'),dict): data_id=str(body['data'].get('id',''))
    values={}
    for part in signature.split(','):
        if '=' in part:
            k,v=part.strip().split('=',1); values[k.strip()]=v.strip()
    ts=values.get('ts',''); received=values.get('v1','')
    if not ts or not received or not request_id or not data_id: return False
    try:
        tolerance=int(os.environ.get('MERCADO_PAGO_WEBHOOK_TOLERANCE_SECONDS','300'))
        if abs(time.time()-int(ts))>max(30,min(tolerance,3600)): return False
    except Exception: return False
    manifest='id:'+data_id+';request-id:'+request_id+';ts:'+ts+';'
    expected=hmac.new(secret.encode(),manifest.encode(),hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected,received)


def tenant_payment_history(slug, email):
    """Return only payment records belonging to this authenticated tenant."""
    out=[]
    for record in payment_history().values():
        if not isinstance(record,dict):
            continue
        reference=str(record.get('reference',''))
        owner = str(record.get('email','')).lower() == str(email).lower() or reference.startswith(str(slug)+':')
        if not owner:
            continue
        out.append({k:record.get(k) for k in ('payment_id','preference_id','reference','plan','amount','days','status','payment_status','activated_at','activation_until','created_at','updated_at','validation') if k in record})
    return sorted(out, key=lambda x: float(x.get('created_at',0) or 0), reverse=True)[:100]


def tenant_audit_events(slug):
    data=load_json(AUDIT_FILE,{})
    events=data.get(str(slug),[]) if isinstance(data,dict) else []
    return events[-200:] if isinstance(events,list) else []


def tenant_metrics(slug):
    """Conservative metrics derived only from persisted catalog/orders."""
    products=store_products(slug); orders=store_orders(slug)
    paid=[o for o in orders if str(o.get('status','')).casefold()=='pago']
    known_prices=[price_number(p.get('price')) for p in products if price_number(p.get('price'))>0]
    return {'catalog_products':len(products),'orders_total':len(orders),'orders_paid':len(paid),
            'gross_paid':round(sum(price_number(o.get('price')) for o in paid),2),
            'average_paid_order':round(sum(price_number(o.get('price')) for o in paid)/len(paid),2) if paid else None,
            'data_sufficiency':'insufficient' if len(orders)<3 or not paid else 'initial',
            'note':'Métricas calculadas somente com pedidos persistidos; sem estimativas ou projeções.'}


def onboarding_checklist(slug):
    products=store_products(slug); settings=agent_settings(slug); videos=store_videos(slug)
    checks=[
        {'id':'catalog','label':'Cadastre pelo menos um produto com preço e descrição','done':bool(products and any(price_number(p.get('price'))>0 for p in products))},
        {'id':'image','label':'Adicione imagens aos produtos principais (opcional)','done':any(bool(p.get('image')) for p in products)},
        {'id':'persona','label':'Revise o nome, tom e orientação da sua Consultora IA','done':bool(settings.get('persona_name') and settings.get('persona_description'))},
        {'id':'video','label':'Publique um vídeo curto para apresentar um produto (opcional)','done':bool(videos)},
        {'id':'link','label':'Copie e compartilhe o link público da loja','done':False},
    ]
    return {'checks':checks,'ready':sum(1 for c in checks if c['done'])>=2,'completed':sum(1 for c in checks if c['done']),'total':len(checks)}


def privacy_trust(slug):
    return {'ai_profile':'A Consultora usa catálogo, regras de margem, tom e histórico de conversa da loja para responder. Não cria produtos, preços ou descontos que não estejam confirmados.',
            'data_use':'Os dados persistidos são usados para catálogo, pedidos, atendimento e auditoria da própria loja.',
            'memory':'A memória de atendimento é separada por loja e sessão de cliente; o modo Concierge não adiciona memória comercial.',
            'controls':'Você pode revisar as regras da IA e consultar o histórico de auditoria no painel.',
            'deletion':'Controles de exclusão/exportação completos ainda não estão disponíveis nesta fase; não prometemos uma API que não existe.',
            'sensitive_data':'O registro de auditoria não deve conter emoções, saúde, inferências sensíveis ou conteúdo desnecessário.'}


def marketplace_summary(slug):
    orders=store_orders(slug); paid=[o for o in orders if o.get('status')=='Pago']; gross=round(sum(price_number(o.get('price')) for o in paid),2); commission=round(sum(float(o.get('commission_amount',round(price_number(o.get('price'))*PLATFORM_COMMISSION_RATE,2))) for o in paid),2); seller_net=round(gross-commission,2)
    return {'orders_paid':len(paid),'gross_sales':gross,'platform_commission':commission,'seller_net_estimate':seller_net,'split_status':'Aguardando conexão OAuth do vendedor'}


def slugify(value):
    value = re.sub(r'[^a-zA-Z0-9À-ÿ]+', '-', value.lower()).strip('-')
    return value[:48] or 'meu-aplicativo'


def infer_type(prompt):
    p = prompt.lower()
    if any(x in p for x in ['loja', 'venda', 'produto', 'catálogo', 'catalogo', 'carrinho']):
        return 'Loja virtual', 'purple'
    if any(x in p for x in ['agenda', 'agendamento', 'salão', 'salao', 'horário', 'horario']):
        return 'Agendamentos', 'blue'
    if any(x in p for x in ['finance', 'despesa', 'conta', 'dinheiro']):
        return 'Controle financeiro', 'green'
    if any(x in p for x in ['curso', 'aula', 'aluno']):
        return 'Curso online', 'pink'
    return 'Aplicativo personalizado', 'purple'


def app_files(name, prompt, kind):
    safe_name = html.escape(name)
    if kind == 'Loja virtual':
        eyebrow, title, sub, cards = 'CATÁLOGO ONLINE', 'Venda mais\ncom simplicidade.', 'Produtos, pedidos e atendimento em um só lugar.', [('Produto destaque','R$ 49,90'), ('Mais vendido','R$ 79,90'), ('Novidade','R$ 99,90')]
    elif kind == 'Agendamentos':
        eyebrow, title, sub, cards = 'AGENDE SEU HORÁRIO', 'Seu tempo.\nSua agenda.', 'Escolha um serviço e reserve seu horário em poucos cliques.', [('Corte e escova','A partir de R$ 60'), ('Manicure','A partir de R$ 35'), ('Consultoria','A partir de R$ 120')]
    elif kind == 'Controle financeiro':
        eyebrow, title, sub, cards = 'CONTROLE FINANCEIRO', 'Veja para onde\nvai seu dinheiro.', 'Organize suas entradas, despesas e metas.', [('Saldo atual','R$ 2.450,00'), ('Entradas do mês','R$ 4.800,00'), ('Despesas','R$ 2.350,00')]
    elif kind == 'Curso online':
        eyebrow, title, sub, cards = 'ÁREA DO ALUNO', 'Aprenda no seu\npróprio ritmo.', 'Conteúdos organizados para você evoluir todos os dias.', [('Módulo 1','Comece aqui'), ('Módulo 2','Em andamento'), ('Módulo 3','Disponível em breve')]
    else:
        eyebrow, title, sub, cards = 'BEM-VINDO', 'Uma ideia que\nganha vida.', 'Seu aplicativo criado a partir de uma ideia simples.', [('Recurso principal','Começar agora'), ('Destaque','Conheça mais'), ('Novidade','Ver detalhes')]
    card_html = ''.join(f'<article class="card"><div class="card-art art-{i}"></div><div class="card-body"><strong>{html.escape(a)}</strong><span>{html.escape(b)}</span><button>Ver detalhes</button></div></article>' for i,(a,b) in enumerate(cards,1))
    index = f'''<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{safe_name}</title><link rel="stylesheet" href="styles.css"></head>
<body><nav><strong class="logo">{safe_name}</strong><a href="#recursos">Recursos</a><a href="#sobre">Sobre</a><a class="nav-cta" href="#contato">Começar</a></nav>
<main><section class="hero"><div><small>{eyebrow}</small><h1>{html.escape(title).replace(chr(10), '<br>')}</h1><p>{html.escape(sub)}</p><button class="cta" onclick="startApp()">Começar agora <span>→</span></button></div><div class="hero-art"><div class="glow"></div><div class="floating-card"><span>✦</span><strong>{safe_name}</strong><small>Seu novo aplicativo</small></div></div></section>
<section id="recursos" class="section"><div class="section-heading"><small>DESTAQUES</small><h2>Tudo organizado para você.</h2></div><div class="cards">{card_html}</div></section>
<section id="sobre" class="about"><div><small>FEITO PARA FACILITAR</small><h2>Uma experiência simples, bonita e pronta para crescer.</h2></div><p>Este aplicativo foi criado a partir da sua ideia: <em>{html.escape(prompt)}</em></p></section>
<section id="contato" class="contact"><h2>Vamos começar?</h2><p>Seu próximo passo está a um clique.</p><button class="cta" onclick="startApp()">Quero começar →</button></section></main><footer>© 2026 {safe_name}. Criado com ForgeAI.</footer><script src="app.js"></script></body></html>'''
    css = '''*{box-sizing:border-box}body{margin:0;background:#0b0912;color:#f8f5ff;font-family:Inter,system-ui,sans-serif}nav{height:70px;display:flex;align-items:center;gap:28px;padding:0 8%;border-bottom:1px solid #282033;background:#0b0912cc;position:sticky;top:0;z-index:2}.logo{font-size:18px;margin-right:auto;background:linear-gradient(90deg,#c084fc,#f472b6);background-clip:text;color:transparent}nav a{color:#aaa3b7;text-decoration:none;font-size:13px}.nav-cta{color:#fff!important;background:#7c3aed;padding:9px 15px;border-radius:20px}.hero{min-height:520px;padding:80px 12%;display:flex;align-items:center;justify-content:space-between;background:radial-gradient(circle at 75% 48%,#6332a545,transparent 35%)}small{color:#c084fc;letter-spacing:2px;font-weight:800;font-size:10px}.hero h1{font-size:clamp(40px,6vw,72px);line-height:.98;letter-spacing:-3px;margin:18px 0;background:linear-gradient(90deg,#fff,#c084fc,#f9a8d4);background-clip:text;color:transparent}.hero p{max-width:430px;color:#aaa3b7;line-height:1.7}.cta{border:0;border-radius:8px;background:linear-gradient(100deg,#7c3aed,#db2777);color:#fff;padding:14px 20px;font-weight:800;box-shadow:0 12px 30px #7c3aed38;cursor:pointer}.cta span{font-size:18px;margin-left:8px}.hero-art{width:320px;height:300px;position:relative}.glow{position:absolute;inset:25px;border-radius:50%;background:#9333ea45;filter:blur(35px)}.floating-card{position:absolute;left:40px;top:65px;width:230px;height:150px;border:1px solid #b173ed66;border-radius:14px;background:linear-gradient(135deg,#28173d,#171226);box-shadow:0 20px 50px #0009;padding:24px;transform:rotate(8deg);display:flex;flex-direction:column;gap:10px}.floating-card span{color:#f472b6;font-size:25px}.floating-card strong{font-size:18px}.floating-card small{font-size:10px;letter-spacing:0;color:#aaa3b7}.section{padding:65px 10%;background:#100d18}.section-heading h2,.about h2{font-size:32px;margin:10px 0 28px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card{border:1px solid #302640;border-radius:10px;overflow:hidden;background:#171321}.card-art{height:135px;background:linear-gradient(135deg,#5b2a74,#261a43)}.art-2{background:linear-gradient(135deg,#174768,#162a52)}.art-3{background:linear-gradient(135deg,#71315b,#321b4c)}.card-body{padding:17px;display:grid;gap:8px}.card-body strong{font-size:15px}.card-body span{color:#a9a1b5;font-size:12px}.card-body button{border:0;background:transparent;color:#c084fc;text-align:left;padding:5px 0;font-weight:700}.about{padding:75px 10%;display:grid;grid-template-columns:1fr 1fr;gap:60px}.about p{color:#aaa3b7;line-height:1.8}.about em{color:#d8b4fe}.contact{text-align:center;padding:75px 20px;background:linear-gradient(120deg,#241239,#171023)}.contact h2{font-size:34px;margin:0 0 10px}.contact p{color:#aaa3b7;margin-bottom:25px}footer{text-align:center;padding:25px;color:#716a7e;font-size:11px}@media(max-width:650px){nav{padding:0 20px;gap:12px}nav a:not(.nav-cta){display:none}.hero{padding:65px 25px;display:block}.hero-art{margin:30px auto 0;width:280px}.cards{grid-template-columns:1fr}.about{display:block;padding:55px 25px}.about p{margin-top:25px}.section{padding:55px 25px}}'''
    js = "function startApp(){alert('Obrigado! Esta ação pode ser conectada ao WhatsApp, formulário ou checkout.');}"
    return {'index.html':index,'styles.css':css,'app.js':js,'forgeai.json':json.dumps({'name':name,'prompt':prompt,'type':kind,'created_at':time.strftime('%Y-%m-%dT%H:%M:%S')},ensure_ascii=False,indent=2)}


def gemini_files(prompt, name):
    """Gera arquivos com Gemini quando GEMINI_API_KEY está configurada.
    Se a API não estiver disponível, o chamador usa o gerador local seguro.
    """
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        return None
    instruction = f'''Você é o motor de criação do ForgeAI. Crie um aplicativo web responsivo a partir desta ideia: {prompt}
Nome do app: {name}
Responda SOMENTE com JSON válido, sem markdown, neste formato exato:
{{"name":"{name}","type":"tipo curto","files":{{"index.html":"...","styles.css":"...","app.js":"..."}}}}
Regras: gere exatamente os três arquivos; use HTML sem bibliotecas externas; o index deve referenciar styles.css e app.js; inclua uma experiência bonita, funcional e mobile-first; não use explicações fora do JSON.
Se a ideia mencionar VendaCertaAI, NÃO crie somente uma landing page. Crie um protótipo navegável completo em uma única aplicação, com telas/seções acionadas por botões para: tela inicial sem planos, Entrar, Iniciar teste grátis de 48 horas, loja pública, catálogo de produtos, conversa da vendedora somente por texto, pedidos, Meu acesso, códigos Básico/Pro/Enterprise e Central ADM. Use localStorage para simular dados e implemente navegação entre as telas. Inclua o slogan “Sua vendedora profissional com IA”, visual escuro neon roxo/rosa/azul e o Instagram https://www.instagram.com/geracao_ricabr?stkn=MXI0ZDlndDg1Yjk1aQ==.''' 
    payload = json.dumps({'contents':[{'parts':[{'text':instruction}]}], 'generationConfig':{'temperature':0.35,'maxOutputTokens':12000}}).encode()
    url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key=' + quote(key)
    try:
        req = urllib.request.Request(url, data=payload, headers={'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=90) as response:
            data=json.loads(response.read().decode('utf-8'))
        text=data['candidates'][0]['content']['parts'][0]['text'].strip()
        text=re.sub(r'^```(?:json)?\\s*|\\s*```$','',text).strip()
        result=json.loads(text)
        files=result.get('files',{})
        if all(k in files and isinstance(files[k],str) for k in ('index.html','styles.css','app.js')):
            return {'files':files,'type':result.get('type','Aplicativo com IA'),'name':result.get('name',name)}
    except Exception:
        return None
    return None


def gemini_modify(command, files):
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        return None
    instruction = '''Você é um editor de código. Altere o projeto abaixo conforme o pedido do usuário.
Responda SOMENTE com JSON válido no formato {"files":{"index.html":"...","styles.css":"...","app.js":"..."}}.
Mantenha os três arquivos, não use markdown e não remova funções que já existem sem necessidade.
PEDIDO: ''' + command + '\\nARQUIVOS: ' + json.dumps(files, ensure_ascii=False)
    payload=json.dumps({'contents':[{'parts':[{'text':instruction}]}], 'generationConfig':{'temperature':0.25,'maxOutputTokens':16000}}).encode()
    url='https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key='+quote(key)
    try:
        req=urllib.request.Request(url,data=payload,headers={'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=90) as response: data=json.loads(response.read().decode('utf-8'))
        text=data['candidates'][0]['content']['parts'][0]['text'].strip()
        text=re.sub(r'^```(?:json)?\\s*|\\s*```$','',text).strip()
        result=json.loads(text); updated=result.get('files',{})
        if all(k in updated and isinstance(updated[k],str) for k in ('index.html','styles.css','app.js')): return updated
    except Exception:
        return None
    return None


def gemini_seller(message, history=None, catalog=None, settings=None):
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        return None
    history = history or []
    catalog_list = catalog if isinstance(catalog,list) and catalog else DEFAULT_PRODUCTS
    catalog = '; '.join(f"{p.get('name','Produto')} — {p.get('price','Preço não informado')} — {p.get('desc','')}" for p in catalog_list if isinstance(p,dict))
    settings = {**DEFAULT_AGENT_SETTINGS, **(settings if isinstance(settings,dict) else {})}
    concierge = bool(re.search(r'\b(presente|presentei|presentear|aniversário|aniversario|casamento|natal|dia das mães|dia das maes|dia dos pais|ocasião|ocasiao|lembrança|lembranca)\b', message.lower()))
    concierge_rule = "Se a mensagem indicar presente ou ocasião, ative o modo concierge: faça no máximo 2 ou 3 perguntas curtas (ocasião, para quem/estilo e faixa de preço, somente se necessário). Depois recomende apenas itens que existam no catálogo, citando exatamente nome e preço cadastrados. Se não houver combinação, diga isso e ofereça opções reais próximas. Pode redigir uma mensagem de presente curta como rascunho, sem afirmar sentimentos ou características da pessoa. Não peça nem guarde dados emocionais sensíveis."
    prompt = "Você é a Consultora Inteligente da Equipe de Vendas com IA da VendaCertaAI. Responda em português brasileiro, de forma simpática, objetiva, personalizada e persuasiva, somente por texto. Você se chama " + str(settings['persona_name']) + " e deve seguir esta descrição: " + str(settings['persona_description']) + ". Use exclusivamente o catálogo e a memória da conversa para recomendar produtos e preços. Sugira complementos somente quando fizer sentido. Nunca invente produtos, preços, descontos, estoque, prazo, recursos ou políticas. Nunca ofereça desconto por conta própria; negociações devem respeitar as regras e aprovação. Se não souber algo, diga que precisa confirmar com o vendedor. " + concierge_rule + '\nREGRAS DA LOJA: margem mínima de ' + str(settings['min_margin']) + "%; desconto máximo de " + str(settings['max_discount']) + "%; descontos exigem aprovação: " + ('sim' if settings['require_approval'] else 'não') + "; tom: " + str(settings['tone']) + ".\nCATÁLOGO: " + catalog + '\nCONVERSA: ' + json.dumps(history[-8:], ensure_ascii=False) + '\nMODO CONCIERGE ATIVO: ' + ('sim' if concierge else 'não') + '\nCLIENTE: ' + message
    payload = json.dumps({'contents':[{'parts':[{'text':prompt}]}], 'generationConfig':{'temperature':0.35,'maxOutputTokens':500}}).encode()
    url = 'https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-flash-lite:generateContent?key=' + quote(key)
    try:
        req = urllib.request.Request(url, data=payload, headers={'Content-Type':'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode('utf-8'))
        return data['candidates'][0]['content']['parts'][0]['text'].strip()
    except Exception:
        return None


def vendacerta_files():
    html_doc='''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VendaCertaAI</title><link rel="stylesheet" href="styles.css"></head><body><div class="beta-banner" role="status"><strong>Beta aberta</strong><span>Teste o fluxo com dados reais do catálogo. O armazenamento atual pode ser temporário até a conexão de um banco durável.</span></div><header><div class="logo">✦ Venda<span>CertaAI</span></div><button class="outline back-btn" id="backBtn">← Voltar</button><nav><button data-view="home">Início</button><button data-view="store">Minha loja</button><button data-view="productsView">Produtos</button><button data-view="assistant">Vendedora IA</button><button data-view="videos">Vídeos & Clips</button><button data-view="orders">Pedidos</button></nav><span id="accountBadge" class="account-badge"></span><button class="theme-btn" id="themeBtn" aria-label="Alternar tema">◐</button><button class="outline" id="headerLogin">Entrar</button></header><main><section id="home" class="view active"><div class="hero"><small>SUA VENDEDORA PROFISSIONAL COM IA</small><h1>Venda mais.<br><em>Atenda melhor.</em></h1><p>Crie sua loja, mostre seus produtos e deixe a VendaCertaAI conversar com seus clientes.</p><div class="actions"><button class="primary" id="trialBtn" aria-describedby="trialHelp">Iniciar teste grátis</button><button class="outline" id="loginBtn">Entrar</button><small id="trialHelp" class="action-help">48 horas para testar; depois, o acesso depende de plano ativo.</small><button class="outline" id="planAccessBtn" style="display:none">Ver planos e acesso</button></div><button class="adm-link" id="admBtn">ADM</button></div></section><section id="store" class="view"><div class="section-head"><div><small>LOJA PÚBLICA</small><h2>Loja da VendaCertaAI</h2><p>Produtos em destaque para seus clientes.</p></div><div class="actions"><button class="outline" id="shareStore">Compartilhar loja</button><button class="primary" id="openChat">Falar com a vendedora</button></div></div><div class="products" id="products"></div></section><section id="videos" class="view"><div class="section-head"><div><small>CONTEÚDO QUE VENDE</small><h2>Vídeos & Clips</h2><p>Descubra produtos em vídeos curtos, pensados para o celular.</p></div><button class="outline" id="openChatFromVideos">Falar com a vendedora</button></div><div class="video-feed" id="videoFeed"><div class="empty">Carregando clips da loja…</div></div><div class="video-admin" id="videoAdmin"><div class="section-head"><div><small>ÁREA DO EMPREENDEDOR</small><h3>Publique um clip</h3><p class="muted">Vídeos MP4, WebM ou MOV até 320 KB após a compressão.</p></div></div><label class="image-picker">Vídeo do produto<input id="videoFile" type="file" accept="video/mp4,video/webm,video/quicktime"><span id="videoFileHint">Escolha um vídeo curto do celular</span></label><select id="videoProduct"><option value="">Selecione o produto</option></select><input id="videoCaption" maxlength="500" placeholder="Legenda do clip (opcional)"><button class="primary" id="saveVideo">Publicar clip</button><div class="video-admin-list" id="videoAdminList"></div></div></section><section id="productsView" class="view"><div class="section-head"><div><small>CATÁLOGO</small><h2>Meus produtos</h2><p>Cadastre e organize seus produtos.</p></div><button class="primary" id="addProduct">＋ Adicionar produto</button></div><div class="products" id="productsAdmin"></div></section><section id="orders" class="view"><div class="section-head"><div><small>GESTÃO</small><h2>Pedidos</h2><p>Acompanhe seus pedidos e sua comissão.</p></div></div><div class="finance-summary" id="marketplaceSummary"><div><small>VENDAS PAGAS</small><strong>R$ 0,00</strong></div><div><small>SUA COMISSÃO ESTIMADA</small><strong>R$ 0,00</strong></div><div><small>REPASSE ESTIMADO</small><strong>R$ 0,00</strong></div></div><div class="orders" id="ordersList"><div class="empty">Nenhum pedido ainda. Os pedidos dos clientes aparecerão aqui.</div></div></section><section id="assistant" class="view"><div class="assistant-box"><div class="ai-identity"><div class="ai-avatar">✦</div><div><small>EQUIPE DE VENDAS COM IA</small><h2>Consultora inteligente</h2><p>Personalizada para esta loja e seus clientes.</p></div><span class="status">● Online</span></div><div class="chat" id="chat"><div class="bubble ai">Olá! Sou a vendedora da sua loja. Como posso ajudar?</div></div><form id="chatForm"><input id="chatInput" placeholder="Digite sua dúvida..." autocomplete="off"><button class="primary">Enviar</button></form><div class="agent-controls" id="agentControls"><div><small>REGRAS DA CONSULTORA</small><h3>Limites de autonomia</h3><p class="muted">Defina o que a IA pode considerar antes de oferecer uma condição comercial.</p></div><div class="agent-fields"><label>Margem mínima (%)<input id="minMargin" type="number" min="0" max="100" step="0.5"></label><label>Desconto máximo (%)<input id="maxDiscount" type="number" min="0" max="100" step="0.5"></label><label>Tom de atendimento<select id="agentTone"><option value="consultivo">Consultivo</option><option value="direto">Direto</option><option value="acolhedor">Acolhedor</option></select></label></div><div class="agent-fields"><label>Nome da vendedora<input id="agentPersonaName" maxlength="60" placeholder="Consultora Certa"></label><label>Descrição da personalidade<input id="agentPersonaDescription" maxlength="180" placeholder="Atenciosa, clara e honesta"></label></div><p class="muted">A consultora usa estas preferências apenas nesta loja. Ela nunca inventa informações do catálogo.</p><label class="check-row"><input id="approvalRequired" type="checkbox"> Exigir minha aprovação para descontos</label><button class="outline" id="saveAgentSettings" type="button">Salvar regras da IA</button><span class="muted" id="agentSettingsStatus"></span></div></div></section><section id="access" class="view"><div class="access-card"><small>MEU ACESSO</small><h2 id="accessTitle">Teste grátis ativo</h2><p id="accessMessage">Você tem 48 horas para testar a VendaCertaAI.</p><div class="timer" id="accessTimer">48:00:00</div><div id="planOptions" style="display:none"><p class="muted">Escolha como deseja continuar usando a plataforma:</p><div class="plan-list"><button class="outline" data-plan="BASICO">Básico · R$ 98,90 · 30 dias</button><button class="outline" data-plan="PRO">Pro · R$ 489,90 · 180 dias</button><button class="outline" data-plan="ENTERPRISE">Enterprise · R$ 1.089,90 · 365 dias</button></div></div><button class="primary" id="codeBtn">Inserir código de acesso</button><p class="muted">Fale com Murilo pelo Instagram para adquirir acesso.</p><a href="https://www.instagram.com/geracao_ricabr?stkn=MXI0ZDlndDg1Yjk1aQ==" target="_blank">Abrir Instagram →</a></div></section><section id="admin" class="view"><div class="section-head"><div><small>CENTRAL ADM</small><h2>Painel do proprietário</h2><p>Gerencie empreendedores e códigos de acesso.</p></div><button class="primary" id="newCode">＋ Gerar código</button></div><div class="admin-grid"><div><b>Empreendedores</b><strong>1</strong><small>Conta cadastrada</small></div><div><b>Códigos disponíveis</b><strong id="codeCount">3</strong><small>Básico, Pro e Enterprise</small></div><div><b>Testes ativos</b><strong>1</strong><small>Em andamento</small></div></div><div class="code-list" id="codeList"><div><span>BÁSICO · 30 dias</span><b>DISPONÍVEL</b></div><div><span>PRO · 180 dias</span><b>DISPONÍVEL</b></div><div><span>ENTERPRISE · 365 dias</span><b>DISPONÍVEL</b></div></div></section></main><div id="modal" class="modal"><div class="modal-card"><button id="closeModal" class="close">×</button><div id="modalBody"></div></div></div><div id="toast" class="toast"></div><script src="app.js"></script></body></html>'''
    css='''*{box-sizing:border-box}body{margin:0;background:#09090f;color:#f8f7ff;font-family:Inter,Arial,sans-serif}button,input{font:inherit}button{cursor:pointer;border:0;color:inherit}.logo{font-size:20px;font-weight:800}.logo span{color:#a78bfa}header{height:70px;border-bottom:1px solid #2a2638;display:flex;align-items:center;padding:0 7%;gap:28px;background:#0d0d15}header nav{display:flex;gap:18px;margin:auto}header nav button,.adm-link{background:transparent;color:#aaa7ba;font-size:12px}.outline{background:transparent;border:1px solid #66508b;padding:10px 15px;border-radius:7px;color:#d8b4fe}.back-btn{padding:8px 12px;font-size:12px}.account-badge{color:#c084fc;font-size:11px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.primary{background:linear-gradient(105deg,#7c3aed,#db2777);border-radius:7px;padding:11px 15px;font-weight:bold}.view{display:none;max-width:1200px;margin:auto;padding:55px 7%}.view.active{display:block}.hero{min-height:570px;display:flex;flex-direction:column;justify-content:center;background:radial-gradient(circle at 75% 35%,#68359a55,transparent 35%)}small{color:#c084fc;letter-spacing:1.5px;font-size:10px;font-weight:bold}.hero h1{font-size:clamp(42px,7vw,76px);line-height:.96;margin:17px 0;letter-spacing:-3px}.hero em{font-style:normal;background:linear-gradient(90deg,#c084fc,#f472b6);background-clip:text;color:transparent}.hero p{color:#aaa7b9;line-height:1.6;max-width:440px}.actions{display:flex;gap:10px;margin-top:22px}.adm-link{margin-top:40px;text-decoration:underline}.section-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:25px}.section-head h2{font-size:29px;margin:8px 0}.section-head p,.muted{color:#908da2;font-size:12px;margin:0}.products{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.product{background:#11111a;border:1px solid #2a2638;border-radius:10px;overflow:hidden}.product-art{height:150px;background:linear-gradient(135deg,#39205d,#a052ad)}.product:nth-child(2) .product-art{background:linear-gradient(135deg,#133b58,#5bc3da)}.product:nth-child(3) .product-art{background:linear-gradient(135deg,#58203c,#ed6ca9)}.product-info{padding:14px}.product-info strong{display:block}.product-info p{color:#aaa7b9;font-size:11px}.product-info .price{color:#c084fc;font-weight:bold;margin:10px 0}.orders .empty{border:1px dashed #463859;color:#9995a8;padding:35px;text-align:center;border-radius:9px}.assistant-box,.access-card{max-width:650px;margin:auto;background:#11111a;border:1px solid #302541;border-radius:12px;padding:25px}.status{color:#4ade80;font-size:11px}.chat{height:320px;overflow:auto;background:#0c0c13;border-radius:8px;padding:15px;margin:15px 0}.bubble{max-width:80%;padding:10px;border-radius:8px;margin:8px 0;font-size:12px}.bubble.ai{background:#281b3c}.bubble.user{background:#2a2937;margin-left:auto}.chat form{display:flex;gap:8px}.chat input{flex:1;background:#0b0b12;border:1px solid #39334a;color:#fff;padding:11px;border-radius:7px;outline:0}.timer{font-size:38px;color:#c084fc;margin:22px 0}.access-card a{color:#f472b6;font-size:12px}.plan-list{display:grid;gap:8px;margin:14px 0}.plan-list button{text-align:left}.admin-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.admin-grid>div{background:#11111a;border:1px solid #2a2638;border-radius:9px;padding:18px}.admin-grid b,.admin-grid strong,.admin-grid small{display:block}.admin-grid strong{font-size:27px;margin:12px 0}.admin-grid small{color:#908da2;font-size:11px}.code-list{margin-top:18px}.code-list>div{display:flex;justify-content:space-between;padding:14px;border-bottom:1px solid #292438;background:#11111a}.code-list b{color:#4ade80;font-size:10px}.modal{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;padding:20px;z-index:5}.modal.open{display:flex}.modal-card{background:#171522;border:1px solid #5a3b78;border-radius:12px;padding:25px;width:min(500px,100%);position:relative}.close{position:absolute;right:14px;top:9px;background:transparent;color:#aaa7ba;font-size:25px}.modal-card h2{margin:5px 0 14px}.modal-card input{display:block;width:100%;background:#0c0c13;border:1px solid #39334a;color:#fff;padding:11px;border-radius:7px;margin:9px 0 13px}.finance-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0 22px}.finance-summary>div{padding:14px;border:1px solid #3c3150;border-radius:9px;background:#11111a}.finance-summary strong,.finance-summary small{display:block}.finance-summary strong{font-size:20px;margin-top:8px;color:#c084fc}.agent-controls{display:none;margin-top:18px;padding:18px;border:1px solid #3c3150;border-radius:10px;background:#0d0d15}.agent-controls.visible{display:block}.agent-controls h3{margin:7px 0 4px}.agent-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}.agent-fields label{color:#aaa7b9;font-size:11px}.agent-fields input,.agent-fields select{display:block;width:100%;margin-top:6px;background:#0b0b12;border:1px solid #39334a;color:#fff;padding:9px;border-radius:7px}.check-row{display:flex;gap:8px;align-items:center;color:#c9c4d4;font-size:11px;margin:12px 0}.agent-controls button{margin-top:8px}.agent-controls #agentSettingsStatus{margin-left:10px}.toast{position:fixed;bottom:22px;right:22px;background:#292139;border:1px solid #7653a8;border-radius:7px;padding:12px;transform:translateY(100px);opacity:0;transition:.3s}.toast.show{transform:translateY(0);opacity:1}@media(max-width:700px){header{padding:0 16px;gap:12px}header nav{display:none}.view{padding:35px 18px}.hero{min-height:500px}.products{grid-template-columns:1fr}.section-head{display:block}.section-head button{margin-top:15px}.admin-grid{grid-template-columns:1fr}.agent-fields{grid-template-columns:1fr}.finance-summary{grid-template-columns:1fr}.hero h1{font-size:49px}}
:root{--navy:#0f172a;--navy-soft:#172554;--purple:#6d5df5;--mint:#5eead4;--cyan:#22d3ee;--ice:#f5f7fa;--ink:#111827;--muted:#64748b;--line:#e2e8f0;--glass:rgba(255,255,255,.76)}
body{background:var(--ice);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:-.01em}.public-storefront header nav,.public-storefront #backBtn,.public-storefront #headerLogin,.public-storefront #themeBtn,.public-storefront #accountBadge,.public-storefront #admBtn,.public-storefront #shareStore,.public-storefront #trialBtn,.public-storefront #loginBtn,.public-storefront #planAccessBtn{display:none!important}.public-storefront header{justify-content:flex-start}.public-storefront .view:not(#store):not(#videos):not(#assistant){display:none!important}.public-storefront #store{display:block}.public-storefront #openChat{display:inline-flex}header{height:74px;padding:0 6%;gap:16px;background:rgba(255,255,255,.82);border-bottom:1px solid rgba(148,163,184,.22);box-shadow:0 8px 30px rgba(15,23,42,.05);backdrop-filter:blur(18px);position:sticky;top:0;z-index:4}.logo{color:var(--navy);font-size:19px;letter-spacing:-.04em}.logo span{background:linear-gradient(100deg,var(--purple),var(--cyan));background-clip:text;color:transparent}header nav{gap:8px}header nav button,.adm-link{color:#64748b;border-radius:10px;padding:9px 12px;transition:.2s}header nav button:hover{color:var(--navy);background:#eef2ff}.outline{border:1px solid #cbd5e1;background:rgba(255,255,255,.6);color:var(--navy);border-radius:10px;padding:10px 15px;transition:.2s}.outline:hover{border-color:var(--purple);box-shadow:0 8px 18px rgba(109,93,245,.14)}.primary{background:linear-gradient(110deg,var(--purple),#8b5cf6);border-radius:11px;padding:11px 16px;box-shadow:0 10px 24px rgba(109,93,245,.2);transition:transform .2s,box-shadow .2s}.primary:hover{transform:translateY(-2px);box-shadow:0 14px 28px rgba(109,93,245,.3)}.theme-btn{border:1px solid #cbd5e1;background:#fff;color:var(--navy);width:36px;height:36px;border-radius:10px;font-size:17px}.account-badge{color:var(--purple);font-weight:700}.view{max-width:1240px;padding:64px 6%}.hero{min-height:590px;border-radius:0 0 32px 32px;padding:72px 7%;background:radial-gradient(circle at 80% 25%,rgba(109,93,245,.18),transparent 28%),radial-gradient(circle at 68% 70%,rgba(34,211,238,.12),transparent 24%)}.hero h1{font-size:clamp(46px,7vw,82px);color:var(--navy);letter-spacing:-.07em}.hero em{background:linear-gradient(100deg,var(--purple),#0891b2);background-clip:text}.hero p,.section-head p,.muted{color:var(--muted)}small{color:#6256d9;letter-spacing:1.4px}.section-head{align-items:center}.section-head h2{color:var(--navy);letter-spacing:-.04em}.products{gap:20px}.product{background:var(--glass);border:1px solid rgba(148,163,184,.3);border-radius:20px;box-shadow:0 14px 35px rgba(15,23,42,.07);backdrop-filter:blur(14px);transition:transform .25s,box-shadow .25s}.product:hover{transform:translateY(-5px);box-shadow:0 20px 42px rgba(15,23,42,.13)}.product-art{height:170px;background:linear-gradient(135deg,#c7d2fe,#ddd6fe 48%,#bae6fd)}.product-art.has-image{background:#eef2ff;overflow:hidden}.product-art img{width:100%;height:100%;object-fit:cover;display:block}.product:nth-child(2) .product-art{background:linear-gradient(135deg,#cffafe,#99f6e4)}.product:nth-child(3) .product-art{background:linear-gradient(135deg,#e0e7ff,#f5d0fe)}.product-info{padding:18px}.product-info strong{color:var(--navy);font-size:15px}.product-info p{color:var(--muted);line-height:1.5}.product-info .price{color:var(--purple);font-size:17px}.product-info .actions{margin-top:14px}.product-info .actions .outline{padding:8px 11px;font-size:12px}.assistant-box,.access-card{background:rgba(255,255,255,.76);border:1px solid rgba(148,163,184,.3);box-shadow:0 20px 50px rgba(15,23,42,.08);backdrop-filter:blur(18px);border-radius:24px}.ai-identity{display:flex;align-items:center;gap:14px}.ai-identity h2{margin:4px 0;color:var(--navy);letter-spacing:-.04em}.ai-identity p{color:var(--muted);font-size:12px;margin:0}.ai-avatar{width:52px;height:52px;border-radius:17px;display:grid;place-items:center;color:#fff;font-size:25px;background:linear-gradient(135deg,var(--purple),var(--cyan));box-shadow:0 10px 24px rgba(109,93,245,.28)}.chat{background:rgba(241,245,249,.8);border:1px solid var(--line)}.bubble.ai{background:#e0e7ff;color:#1e1b4b;border-radius:14px}.bubble.user{background:var(--navy);border-radius:14px}.status{margin-left:auto;color:#059669}.chat input{background:#fff;border:1px solid var(--line);color:var(--ink)}.admin-grid>div,.code-list>div{background:rgba(255,255,255,.78);border-color:var(--line);border-radius:14px}.admin-grid strong{color:var(--navy)}.modal-card{background:rgba(255,255,255,.94);color:var(--ink);border:1px solid #cbd5e1;border-radius:20px;box-shadow:0 24px 80px rgba(15,23,42,.2)}.modal-card input{background:#f8fafc;border-color:#cbd5e1;color:var(--ink);border-radius:10px}.image-picker{display:block;color:var(--muted);font-size:12px;margin:12px 0}.image-picker input{display:block;width:100%;margin-top:7px;padding:10px;border:1px dashed #94a3b8;border-radius:10px;background:#f8fafc;color:var(--ink)}.image-picker span{display:block;font-size:11px;margin-top:6px}.product-image-preview{height:150px;margin:10px 0;border-radius:12px;overflow:hidden;background:#eef2ff}.product-image-preview img{width:100%;height:100%;object-fit:cover}.close{color:var(--muted)}.toast{background:var(--navy);border-color:var(--purple);border-radius:12px}.dark{background:#111827;color:#e5e7eb}.dark header{background:rgba(17,24,39,.84);border-color:#293548}.dark .logo,.dark .section-head h2,.dark .hero h1,.dark .product-info strong,.dark .ai-identity h2{color:#f8fafc}.dark .view{color:#e5e7eb}.dark .product,.dark .assistant-box,.dark .access-card,.dark .admin-grid>div,.dark .code-list>div,.dark .modal-card{background:rgba(31,41,55,.82);border-color:#374151}.dark .product-info p,.dark .section-head p,.dark .muted,.dark .ai-identity p{color:#94a3b8}.dark .chat{background:#111827;border-color:#374151}.dark .chat input{background:#1f2937;border-color:#475569;color:#fff}.dark .bubble.ai{background:#312e81;color:#e0e7ff}.beta-banner{display:flex;align-items:center;justify-content:center;gap:10px;padding:9px 16px;background:#fff7ed;border-bottom:1px solid #fed7aa;color:#7c2d12;font-size:12px;text-align:center}.beta-banner strong{color:#c2410c}.action-help{display:block;color:#64748b;letter-spacing:0;font-size:11px;margin-top:10px}.actions{display:flex;flex-wrap:wrap;align-items:center;gap:10px}.actions .action-help{flex-basis:100%}.is-loading{opacity:.65;pointer-events:none}.error-state{border:1px solid #fecaca;background:#fff1f2;color:#9f1239;border-radius:14px;padding:18px;text-align:center}.dark .beta-banner{background:#422006;border-color:#92400e;color:#fed7aa}.dark .action-help{color:#94a3b8}.dark .error-state{background:#450a0a;border-color:#991b1b;color:#fecdd3}.dark .theme-btn,.dark .outline{background:#1f2937;border-color:#475569;color:#e2e8f0}@media(max-width:700px){.beta-banner{display:block;font-size:11px;line-height:1.4}.beta-banner span{display:block;margin-top:2px}header{height:auto;min-height:64px;padding:10px 14px;gap:8px;flex-wrap:wrap}.logo{margin-right:auto}.account-badge{display:none}header nav{order:3;flex-basis:100%;display:flex;overflow-x:auto;padding-bottom:2px;scrollbar-width:none}header nav::-webkit-scrollbar{display:none}header nav button{white-space:nowrap;padding:7px 9px}.theme-btn{width:32px;height:32px}.back-btn{padding:7px 9px;font-size:11px}.hero{border-radius:0;padding:48px 22px}.view{padding:38px 18px}.ai-identity{align-items:flex-start}.status{font-size:10px}.actions .primary,.actions .outline{flex:1;min-width:130px}.action-help{flex-basis:100%!important}}
'''
    css += '''
.video-feed{display:flex;gap:18px;overflow-x:auto;scroll-snap-type:x mandatory;padding:8px 2px 22px;min-height:180px}.video-card{flex:0 0 min(84vw,300px);scroll-snap-align:start;border-radius:24px;overflow:hidden;background:#0f172a;color:#fff;position:relative;box-shadow:0 18px 38px rgba(15,23,42,.2);min-height:470px}.video-card video{width:100%;height:470px;object-fit:cover;display:block;background:#111827}.video-overlay{position:absolute;inset:auto 0 0;padding:55px 18px 18px;background:linear-gradient(transparent,rgba(2,6,23,.92))}.video-overlay strong{display:block;font-size:17px}.video-overlay p{margin:7px 0;color:#dbeafe;font-size:13px}.video-overlay .price{color:#67e8f9;font-weight:800;margin-bottom:10px}.video-overlay .actions{display:flex}.video-admin{margin-top:26px;padding:22px;border:1px solid rgba(148,163,184,.3);border-radius:20px;background:rgba(255,255,255,.72);display:none}.video-admin.visible{display:block}.video-admin select,.video-admin input{width:100%;margin:7px 0;padding:12px;border:1px solid #cbd5e1;border-radius:10px;background:#fff;color:#111827}.video-admin-list{display:grid;gap:8px;margin-top:18px}.video-admin-item{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:10px;border:1px solid #e2e8f0;border-radius:12px;font-size:12px}.public-storefront #videos{display:block}.public-storefront #videoAdmin{display:none!important}.dark .video-admin{background:rgba(31,41,55,.82);border-color:#374151}.dark .video-admin select,.dark .video-admin input{background:#1f2937;border-color:#475569;color:#fff}.dark .video-admin-item{border-color:#374151}@media(max-width:700px){.video-card{flex-basis:82vw}.video-admin{padding:16px}}
'''
    js=r'''let products=[{name:'Produto especial',desc:'Qualidade e estilo para você.',price:'R$ 49,90'},{name:'Mais vendido',desc:'O favorito dos clientes.',price:'R$ 79,90'},{name:'Novidade',desc:'Acabou de chegar na loja.',price:'R$ 99,90'}];let orders=[];let authUser=null;let storeSlug=new URLSearchParams(window.location.search).get('loja')||'vendacertaai';let customerSession=localStorage.getItem('vc_customer_session_'+storeSlug)||('c_'+Date.now()+'_'+Math.random().toString(36).slice(2));localStorage.setItem('vc_customer_session_'+storeSlug,customerSession);let currentView='home';let screenHistory=['home'];const $=id=>document.getElementById(id);function publicStorefront(){return !authUser&&new URLSearchParams(window.location.search).has('loja')}function applyStorefrontMode(){if(publicStorefront()){document.body.classList.add('public-storefront');view('store',false)}}function view(id,record=true){if((id==='productsView'||id==='orders')&&!authUser){if(publicStorefront()){toast('A gestão fica disponível somente na conta do empreendedor.');return}openLogin();toast('Entre para acessar sua área de gestão.');return}if(!$(id)||id===currentView)return;document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));$(id).classList.add('active');if(record)screenHistory.push(id);currentView=id;window.scrollTo(0,0)}function toast(t){$('toast').textContent=t;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),3200)}function setBusy(el,busy,label){if(!el)return;el.classList.toggle('is-loading',busy);el.disabled=busy;if(busy){el.dataset.originalLabel=el.innerHTML;el.innerHTML=label||'Aguarde…'}else if(el.dataset.originalLabel){el.innerHTML=el.dataset.originalLabel;delete el.dataset.originalLabel}}function modal(html){$('modalBody').innerHTML=html;$('modal').classList.add('open')}function imageSrc(p){const src=String(p?.image||'');return /^(?:data:image\/(?:jpeg|jpg|png|webp|gif);base64,[A-Za-z0-9+/=]+|https:\/\/[^\s<>"']{1,480})$/i.test(src)?src:''}function imageMarkup(p){const src=imageSrc(p);return src?'<div class=\"product-art has-image\"><img src=\"'+src+'\" alt=\"\" loading=\"lazy\" onerror=\"this.parentElement.classList.remove(\'has-image\');this.remove()\"></div>':'<div class=\"product-art\"></div>'}function compressProductImage(file){return new Promise((resolve,reject)=>{if(!file)return resolve('');if(!/^image\/(?:jpeg|png|webp|gif)$/i.test(file.type)||file.size>12*1024*1024)return reject(new Error('Escolha uma imagem JPG, PNG, WEBP ou GIF de até 12 MB.'));const img=new Image(),reader=new FileReader();reader.onload=()=>{img.onload=()=>{const max=1000,scale=Math.min(1,max/Math.max(img.naturalWidth,img.naturalHeight)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(img.naturalWidth*scale));canvas.height=Math.max(1,Math.round(img.naturalHeight*scale));canvas.getContext('2d').drawImage(img,0,0,canvas.width,canvas.height);const data=canvas.toDataURL('image/jpeg',.82);if(data.length>220000)return reject(new Error('A imagem não pôde ser reduzida o suficiente. Escolha uma foto menor.'));resolve(data)};img.onerror=()=>reject(new Error('Não foi possível ler a imagem.'));img.src=reader.result};reader.onerror=()=>reject(new Error('Não foi possível ler a imagem.'));reader.readAsDataURL(file)})}function dismissModal(){ $('modal').classList.remove('open') }function renderProducts(){const html=products.map((p,i)=>`<article class="product">'+imageMarkup(p)+'<div class="product-info"><strong>${safeText(p.name)}</strong><p>${safeText(p.desc)}</p><div class="price">${safeText(p.price)}</div><button class="primary" data-buy="${i}">Comprar via Pix rápido</button></div></article>`).join('');$('products').innerHTML=html;$('productsAdmin').innerHTML=products.map((p,i)=>`<article class="product">'+imageMarkup(p)+'<div class="product-info"><strong>${safeText(p.name)}</strong><p>${safeText(p.desc)}</p><div class="price">${safeText(p.price)}</div><div class="actions"><button class="outline" data-edit="${i}">Editar</button><button class="outline" data-delete="${i}">Excluir</button></div></div></article>`).join('');document.querySelectorAll('[data-buy]').forEach(b=>b.onclick=async()=>{const chosen=products[Number(b.dataset.buy)],order={id:String(Date.now()).slice(-6),product:chosen.name,price:chosen.price,status:'Novo'};if(authUser){orders.push(order);syncOrders();view('orders');renderOrders();toast('Pedido criado com sucesso')}else{const saved=JSON.parse(localStorage.getItem('vc_customer_profile_'+storeSlug)||'{}');modal('<small>PIX RÁPIDO VIA CHECKOUT</small><h2>Comprar com segurança</h2><p class="muted">Informe apenas seu nome e telefone. O Mercado Pago mostrará as opções disponíveis, incluindo Pix quando habilitado. Não cobramos aqui e não usamos Open Finance.</p><input id="customerName" placeholder="Seu nome"><input id="customerPhone" placeholder="Seu telefone"><button class="primary" id="customerSubmit">Enviar pedido</button>');setTimeout(()=>{$('customerName').value=saved.name||'';$('customerPhone').value=saved.phone||'';$('customerSubmit').onclick=async()=>{const name=$('customerName').value.trim(),phone=$('customerPhone').value.trim();if(!name||!phone){toast('Informe nome e telefone');return}try{const r=await fetch('/api/payment/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,session_id:customerSession,customer:{name,phone},order})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Não foi possível criar o pagamento.');localStorage.setItem('vc_customer_profile_'+storeSlug,JSON.stringify({name,phone}));dismissModal();window.location.href=d.checkout_url}catch(e){toast('Não foi possível enviar o pedido agora.')}}},0)}});document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.edit),p=products[i];modal('<small>EDITAR PRODUTO</small><h2>Atualizar produto</h2><label class="image-picker">Imagem do produto<input id="editProductImage" type="file" accept="image/jpeg,image/png,image/webp,image/gif"><span id="editProductImageHint">Selecione uma nova imagem para substituir</span></label><div id="editProductImagePreview" class="product-image-preview" '+(p.image?'':'hidden')+'>'+(p.image?'<img src="'+safeAttr(p.image)+'" alt="Imagem atual">':'')+'</div><button type="button" class="outline" id="removeProductImage" '+(p.image?'':'hidden')+'>Remover imagem atual</button><input id="editName" placeholder="Nome do produto"><input id="editDesc" placeholder="Descrição"><input id="editPrice" placeholder="Preço"><button class="primary" id="updateProduct">Salvar alterações</button>');setTimeout(()=>{let image=p.image||'',removeImage=false;$('editName').value=p.name;$('editDesc').value=p.desc;$('editPrice').value=p.price;$('editProductImage').onchange=async()=>{try{const selected=await compressProductImage($('editProductImage').files[0]);if(selected){image=selected;removeImage=false;$('editProductImagePreview').innerHTML='<img src="'+selected+'" alt="Prévia da nova imagem">';$('editProductImagePreview').hidden=false;$('removeProductImage').hidden=false;$('editProductImageHint').textContent='Nova imagem pronta para salvar'}}catch(e){$('editProductImage').value='';toast(e.message)}};$('removeProductImage').onclick=()=>{image='';removeImage=true;$('editProductImage').value='';$('editProductImagePreview').hidden=true;$('removeProductImage').hidden=true;$('editProductImageHint').textContent='Imagem será removida ao salvar'};$('updateProduct').onclick=async()=>{const name=$('editName').value.trim(),desc=$('editDesc').value.trim(),price=$('editPrice').value.trim();if(!name||!price){toast('Informe nome e preço');return}products[i]={id:p.id,name,desc:desc||'Produto disponível na loja.',price,...(image&&!removeImage?{image}:{})};try{await syncProducts();renderProducts();dismissModal();toast('Produto atualizado!')}catch(e){}}},0)});document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.delete);modal('<small>EXCLUIR PRODUTO</small><h2>Tem certeza?</h2><p class="muted">Este produto será removido da loja.</p><button class="primary" id="confirmDelete">Excluir produto</button>');setTimeout(()=>{$('confirmDelete').onclick=()=>{products.splice(i,1);if(!products.length)products.push({name:'Novo produto',desc:'Adicione uma descrição.',price:'R$ 0,00'});syncProducts();renderProducts();dismissModal();toast('Produto excluído!')}},0)});};async function syncProducts(){localStorage.setItem('vendacerta_products',JSON.stringify(products));try{const r=await fetch('/api/store',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,products})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Catálogo indisponível');if(Array.isArray(d.products)){products=d.products;localStorage.setItem('vendacerta_products',JSON.stringify(products));}return products}catch(e){toast(e.message||'Não foi possível salvar o catálogo.');throw e}}function renderOrders(){if(!orders.length){$('ordersList').innerHTML='<div class="empty">Nenhum pedido ainda. Os pedidos dos clientes aparecerão aqui.</div>';return}$('ordersList').innerHTML=orders.map(o=>'<div class="product"><div class="product-info"><strong>Pedido '+o.id+'</strong><p>'+safeText(o.product)+' · 1 unidade</p><div class="price">'+safeText(o.price)+'</div><span class="muted">Cliente: '+safeText(o.customer_name||'Não informado')+(o.customer_phone?' · '+safeText(o.customer_phone):'')+'<br>Status: '+safeText(o.status||'Novo')+'</span></div></div>').join('')}function syncOrders(){localStorage.setItem('vendacerta_orders',JSON.stringify(orders));fetch('/api/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,orders})}).catch(()=>{})}async function loadVideos(){try{const r=await fetch('/api/videos?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(!r.ok)throw new Error(d.error||'Clips indisponíveis');const list=Array.isArray(d.videos)?d.videos:[];$('videoFeed').innerHTML=list.length?list.map(v=>'<article class="video-card"><video controls playsinline preload="metadata"'+(v.poster?' poster="'+safeAttr(v.poster)+'"':'')+' src="'+safeAttr(v.video)+'"></video><div class="video-overlay"><strong>'+safeText(v.product_name)+'</strong><p>'+safeText(v.caption||'Veja este produto em ação.')+'</p><div class="price">'+safeText(v.price)+'</div><div class="actions"><button class="primary" data-video-buy="'+safeAttr(v.product_id)+'">Ver produto e comprar</button><button class="outline" data-video-chat="'+safeAttr(v.product_name)+'">Tirar dúvida</button></div></div></article>').join(''):'<div class="empty">Ainda não há clips publicados nesta loja.</div>';document.querySelectorAll('[data-video-buy]').forEach(b=>b.onclick=()=>{const i=products.findIndex(p=>String(p.id)===b.dataset.videoBuy);if(i>=0){view('store');document.querySelector('[data-buy=\"'+i+'\"]')?.scrollIntoView({behavior:'smooth',block:'center'})}});document.querySelectorAll('[data-video-chat]').forEach(b=>b.onclick=()=>{view('assistant');$('chatInput').value='Tenho interesse em '+b.dataset.videoChat;$('chatInput').focus()})}catch(e){$('videoFeed').innerHTML='<div class="error-state">Não foi possível carregar os clips agora.</div>'}}function safeAttr(t){return String(t??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function renderVideoAdmin(list){if(!authUser)return;$('videoAdmin').classList.add('visible');$('videoProduct').innerHTML='<option value="">Selecione o produto</option>'+products.map(p=>'<option value="'+safeAttr(p.id)+'">'+safeText(p.name)+' · '+safeText(p.price)+'</option>').join('');$('videoAdminList').innerHTML=(list||[]).slice().reverse().map(v=>'<div class="video-admin-item"><span>'+safeText(v.product_name)+'</span><button class="outline" data-video-delete="'+safeAttr(v.id)+'">Excluir</button></div>').join('');document.querySelectorAll('[data-video-delete]').forEach(b=>b.onclick=async()=>{if(!confirm('Excluir este clip?'))return;const r=await fetch('/api/videos?loja='+encodeURIComponent(storeSlug)+'&id='+encodeURIComponent(b.dataset.videoDelete),{method:'DELETE'});const d=await r.json();if(r.ok){renderVideoAdmin(d.videos);loadVideos();toast('Clip excluído.')}else toast(d.error||'Não foi possível excluir.')})}async function loadVideoAdmin(){if(!authUser)return;try{const r=await fetch('/api/videos?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(r.ok)renderVideoAdmin(d.videos)}catch(e){}}function readVideo(file){return new Promise((resolve,reject)=>{if(!file)return reject(new Error('Escolha um vídeo.'));if(!/^video\/(?:mp4|webm|quicktime)$/i.test(file.type)||file.size>8*1024*1024)return reject(new Error('Escolha MP4, WebM ou MOV de até 8 MB para compressão.'));const fr=new FileReader();fr.onload=()=>{const videoUrl=String(fr.result);if(videoUrl.length>460000)return reject(new Error('O vídeo ficou maior que 320 KB. Escolha um clip bem curto.'));const el=document.createElement('video');el.muted=true;el.preload='metadata';el.src=videoUrl;el.onloadeddata=()=>{try{const canvas=document.createElement('canvas'),scale=Math.min(1,480/Math.max(el.videoWidth,el.videoHeight));canvas.width=Math.max(1,Math.round(el.videoWidth*scale));canvas.height=Math.max(1,Math.round(el.videoHeight*scale));canvas.getContext('2d').drawImage(el,0,0,canvas.width,canvas.height);const poster=canvas.toDataURL('image/jpeg',.65);resolve({video:videoUrl,poster:poster.length<=90000?poster:''})}catch(e){resolve({video:videoUrl,poster:''})}};el.onerror=()=>resolve({video:videoUrl,poster:''});};fr.onerror=()=>reject(new Error('Não foi possível ler o vídeo.'));fr.readAsDataURL(file)})}async function loadProducts(){try{const r=await fetch('/api/store?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(!r.ok)throw new Error(d.error||'Catálogo indisponível');if(d.products?.length){products=d.products;renderProducts();loadVideoAdmin()}}catch(e){const target=$('products');if(target&&!target.children.length)target.innerHTML='<div class="error-state">Não foi possível carregar o catálogo agora.<br><button class="outline" onclick="loadProducts()">Tentar novamente</button></div>';toast('O catálogo não respondeu. Tente novamente.')}}async function loadOrders(){if(!authUser){orders=[];renderOrders();return}try{const r=await fetch('/api/orders?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(Array.isArray(d.orders)){orders=d.orders;renderOrders()}}catch(e){}}async function loadMarketplaceSummary(){if(!authUser)return;try{const r=await fetch('/api/marketplace/summary?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(d.ok){const s=d.summary;$('marketplaceSummary').innerHTML='<div><small>VENDAS PAGAS</small><strong>R$ '+Number(s.gross_sales||0).toFixed(2).replace('.',',')+'</strong></div><div><small>SUA COMISSÃO ESTIMADA</small><strong>R$ '+Number(s.platform_commission||0).toFixed(2).replace('.',',')+'</strong></div><div><small>REPASSE ESTIMADO</small><strong>R$ '+Number(s.seller_net_estimate||0).toFixed(2).replace('.',',')+'</strong></div>'}}catch(e){}}async function loadAccessStatus(){if(!authUser)return;try{const r=await fetch('/api/access/status');const d=await r.json();if(!d.ok)return;const a=d.access;if(a.locked){$('planAccessBtn').style.display='inline-block';$('accessTitle').textContent='Seu teste terminou';if(currentView!=='access')view('access',false);$('accessMessage').textContent='Escolha um plano para continuar usando a VendaCertaAI.';$('accessTimer').style.display='none';$('planOptions').style.display='block'}else{$('planAccessBtn').style.display='none';$('accessTitle').textContent=a.plan_active?'Acesso '+(a.plan||'ativo'):'Teste grátis ativo';$('accessMessage').textContent=a.plan_active?'Seu acesso está ativo.':'Você tem 48 horas para testar a VendaCertaAI.'}}catch(e){}}async function checkSession(){try{const r=await fetch('/api/auth/me',{credentials:'same-origin',cache:'no-store'});const d=await r.json();if(d.ok&&d.user){storeSlug=d.user.slug;setAuth(d.user);window.history.replaceState({},'',window.location.pathname+'?loja='+encodeURIComponent(storeSlug));loadProducts();loadOrders();loadMarketplaceSummary();loadAgentSettings()}else{document.documentElement.classList.remove('vc-auth-loading');document.documentElement.classList.add('vc-anonymous')}}catch(e){document.documentElement.classList.remove('vc-auth-loading');document.documentElement.classList.add('vc-anonymous')}}document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));async function authPost(route,payload){const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error(d.error||'Não foi possível concluir.');return d}function setAuth(user){authUser=user;document.body.classList.toggle('public-storefront',!user&&new URLSearchParams(window.location.search).has('loja'));$('accountBadge').textContent=user?'Olá, '+user.name:'';$('headerLogin').textContent=user?'Sair':'Entrar';$('headerLogin').onclick=user?logout:openLogin;$('agentControls').classList.toggle('visible',!!user);if(user){loadAgentSettings();loadAccessStatus();loadVideoAdmin()}}async function loadAgentSettings(){if(!authUser)return;try{const r=await fetch('/api/agent/settings?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(d.ok){$('minMargin').value=d.settings.min_margin;$('maxDiscount').value=d.settings.max_discount;$('approvalRequired').checked=!!d.settings.require_approval;$('agentTone').value=d.settings.tone;$('agentPersonaName').value=d.settings.persona_name||'Consultora Certa';$('agentPersonaDescription').value=d.settings.persona_description||'Uma vendedora atenciosa, clara e honesta da sua loja.'}}catch(e){}}async function saveAgentSettings(){try{const payload={slug:storeSlug,settings:{min_margin:Number($('minMargin').value),max_discount:Number($('maxDiscount').value),require_approval:$('approvalRequired').checked,tone:$('agentTone').value,persona_name:$('agentPersonaName').value.trim(),persona_description:$('agentPersonaDescription').value.trim()}};const d=await authPost('/api/agent/settings',payload);$('agentSettingsStatus').textContent='Regras salvas';setTimeout(()=>$('agentSettingsStatus').textContent='',2500);toast('Regras da IA atualizadas!')}catch(e){toast(e.message)}}function enterStore(user,message){storeSlug=user.slug;setAuth(user);window.history.replaceState({},'',window.location.pathname+'?loja='+encodeURIComponent(storeSlug));dismissModal();view('store');loadProducts();loadOrders();loadMarketplaceSummary();loadAgentSettings();toast(message)}async function logout(){await fetch('/api/auth/logout',{method:'POST'});setAuth(null);view('home');toast('Você saiu da conta.')}function openSignup(){modal('<small>TESTE GRÁTIS</small><h2>Comece em 48 horas</h2><input id="signupName" placeholder="Seu nome"><input id="signupBusiness" placeholder="Nome do negócio"><input id="signupEmail" placeholder="Seu e-mail" type="email"><input id="signupPassword" placeholder="Crie uma senha (mín. 6 caracteres)" type="password"><button class="primary" id="signupSubmit">Criar minha conta</button>');setTimeout(()=>{$('signupSubmit').onclick=async()=>{try{const d=await authPost('/api/auth/signup',{name:$('signupName').value,business:$('signupBusiness').value,email:$('signupEmail').value,password:$('signupPassword').value});enterStore(d.user,'Teste grátis iniciado!')}catch(e){toast(e.message)} }},0)}function openLogin(){modal('<small>ENTRAR</small><h2>Acesse sua conta</h2><input id="loginEmail" placeholder="E-mail" type="email"><input id="loginPassword" placeholder="Senha" type="password"><button class="primary" id="loginSubmit">Entrar</button>');setTimeout(()=>{$('loginSubmit').onclick=async()=>{try{const d=await authPost('/api/auth/login',{email:$('loginEmail').value,password:$('loginPassword').value});enterStore(d.user,'Login realizado!')}catch(e){toast(e.message)} }},0)}$('trialBtn').onclick=openSignup;$('loginBtn').onclick=$('headerLogin').onclick=openLogin;$('planAccessBtn').onclick=()=>view('access');document.querySelectorAll('[data-plan]').forEach(b=>b.onclick=async()=>{try{const d=await authPost('/api/access/checkout',{plan:b.dataset.plan});if(d.checkout_url)window.location.href=d.checkout_url;else toast('Checkout indisponível.')}catch(e){toast(e.message)}});$('admBtn').onclick=()=>{modal('<small>ÁREA RESTRITA</small><h2>Senha do ADM</h2><input id="admPassword" type="password" placeholder="Digite a senha"><button class="primary" id="admLogin">Acessar ADM</button>');setTimeout(()=>{$('admLogin').onclick=async()=>{try{await authPost('/api/admin/login',{password:$('admPassword').value});dismissModal();view('admin');toast('Sessão ADM iniciada')}catch(e){toast(e.message)}}},0)};function storeLink(){return window.location.origin+window.location.pathname+'?loja='+encodeURIComponent(storeSlug)+'&cliente=1'}$('shareStore').onclick=()=>{const link=storeLink();modal('<small>LINK DA LOJA PÚBLICA</small><h2>Compartilhe sua loja</h2><p class="muted">Envie este link para seus clientes verem os produtos:</p><input id="storeLink" value="'+link+'" readonly><button class="primary" id="copyStore">Copiar link</button><button class="outline" onclick="window.open(\''+link+'\',\'_blank\')">Abrir loja pública</button>');setTimeout(()=>{$('copyStore').onclick=()=>{navigator.clipboard?.writeText(link);toast('Link copiado!');dismissModal()}},0)};$('openChat').onclick=()=>view('assistant');$('codeBtn').onclick=()=>{modal('<small>CÓDIGO DE ACESSO</small><h2>Digite seu código</h2><input id="accessCode" placeholder="Ex.: PRO-2026-XXXX"><button class="primary" id="redeemCode">Liberar acesso</button>');setTimeout(()=>{$('redeemCode').onclick=async()=>{try{await authPost('/api/access/redeem',{code:$('accessCode').value});dismissModal();toast('Código validado! Acesso liberado.');loadAccessStatus()}catch(e){toast(e.message)}}},0)};$('newCode').onclick=()=>{modal('<small>NOVO CÓDIGO</small><h2>Escolha o plano</h2><select id="planSelect"><option value="BASICO">Básico · 30 dias</option><option value="PRO">Pro · 180 dias</option><option value="ENTERPRISE">Enterprise · 365 dias</option></select><button class="primary" id="createCode">Gerar código</button>');setTimeout(()=>{$('createCode').onclick=async()=>{try{const d=await authPost('/api/admin/codes/create',{plan:$('planSelect').value});modal('<small>'+safeText(d.plan)+' · '+d.days+' dias</small><h2>Código criado</h2><p class="muted">Entregue este código ao empreendedor:</p><div class="timer">'+safeText(d.code)+'</div><button class="primary" onclick="dismissModal()">Fechar</button>')}catch(e){toast(e.message)}}},0)};$('addProduct').onclick=()=>{modal('<small>NOVO PRODUTO</small><h2>Adicionar produto</h2><label class="image-picker">Imagem do produto<input id="productImage" type="file" accept="image/jpeg,image/png,image/webp,image/gif"><span id="productImageHint">JPG, PNG ou WEBP · será otimizada no celular</span></label><div id="productImagePreview" class="product-image-preview" hidden></div><input id="productName" placeholder="Nome do produto"><input id="productDesc" placeholder="Descrição"><input id="productPrice" placeholder="Preço"><button class="primary" id="saveProduct">Salvar produto</button>');setTimeout(()=>{let image='';$('productImage').onchange=async()=>{try{image=await compressProductImage($('productImage').files[0]);const preview=$('productImagePreview');preview.innerHTML='<img src="'+image+'" alt="Prévia do produto">';preview.hidden=!image;$('productImageHint').textContent='Imagem pronta para salvar';}catch(e){image='';$('productImage').value='';toast(e.message)}};$('saveProduct').onclick=async()=>{const name=$('productName').value.trim(),desc=$('productDesc').value.trim(),price=$('productPrice').value.trim();if(!name||!price){toast('Informe nome e preço');return}products.push({name,desc:desc||'Produto disponível na loja.',price,...(image?{image}:{})});try{await syncProducts();renderProducts();dismissModal();toast('Produto salvo e catálogo atualizado!')}catch(e){}}},0)};function safeText(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML}$('chatForm').onsubmit=async e=>{e.preventDefault();const i=$('chatInput'),send=e.submitter||$('chatForm').querySelector('button');if(!i.value.trim()||send?.disabled)return;setBusy(send,true,'Consultando…');const t=i.value.trim();const c=$('chat');c.innerHTML+='<div class="bubble user">'+safeText(t)+'</div>';i.value='';c.innerHTML+='<div class="bubble ai" id="typing">Estou consultando os produtos...</div>';c.scrollTop=c.scrollHeight;try{const r=await fetch('/api/seller-chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t,catalog:products,slug:storeSlug,session_id:customerSession})});const d=await r.json();$('typing')?.remove();c.innerHTML+='<div class="bubble ai">'+safeText(d.reply||'Não consegui responder agora.')+'</div>'}catch(err){$('typing')?.remove();c.innerHTML+='<div class="bubble ai">Não consegui falar com a vendedora agora. Tente novamente.</div>';toast('A consultora está indisponível no momento.')}finally{setBusy(send,false)}c.scrollTop=c.scrollHeight};$('saveVideo').onclick=async()=>{try{if(!authUser)throw new Error('Entre para publicar clips.');const media=await readVideo($('videoFile').files[0]),video=media.video,poster=media.poster,product_id=$('videoProduct').value;if(!product_id)throw new Error('Selecione um produto.');const r=await fetch('/api/videos',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,product_id,video,poster,caption:$('videoCaption').value})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Não foi possível publicar.');$('videoFile').value='';$('videoCaption').value='';renderVideoAdmin(d.videos);loadVideos();toast('Clip publicado na sua loja!')}catch(e){toast(e.message)}};$('openChatFromVideos').onclick=()=>view('assistant');$('themeBtn').onclick=()=>{document.body.classList.toggle('dark');localStorage.setItem('vc_theme',document.body.classList.contains('dark')?'dark':'light')};$('saveAgentSettings').onclick=saveAgentSettings;if(localStorage.getItem('vc_theme')==='dark')document.body.classList.add('dark');$('backBtn').onclick=()=>{if(screenHistory.length>1){screenHistory.pop();view(screenHistory[screenHistory.length-1],false)}};$('closeModal').onclick=dismissModal;renderProducts();renderOrders();loadVideos();applyStorefrontMode();loadProducts();loadOrders();checkSession();function showDetailsForm(){const q=new URLSearchParams(window.location.search),oid=q.get('order_id'),token=q.get('details_token');if(!oid||!token)return;modal('<small>PRÓXIMO PASSO DO PEDIDO</small><h2>Complete entrega e contato</h2><p class=\"muted\">Pagamento confirmado ou em análise. Envie estes dados para a loja preparar o pedido.</p><input id=\"detailAddress\" placeholder=\"Rua e endereço\"><input id=\"detailNumber\" placeholder=\"Número\"><input id=\"detailComplement\" placeholder=\"Complemento (opcional)\"><input id=\"detailNeighborhood\" placeholder=\"Bairro\"><input id=\"detailCity\" placeholder=\"Cidade\"><input id=\"detailState\" placeholder=\"Estado (UF)\"><input id=\"detailZip\" placeholder=\"CEP\"><button class=\"primary\" id=\"saveDetails\">Enviar detalhes à loja</button>');setTimeout(()=>{$('saveDetails').onclick=async()=>{const b={slug:storeSlug,order_id:oid,details_token:token,address:{address:$('detailAddress').value,number:$('detailNumber').value,complement:$('detailComplement').value,neighborhood:$('detailNeighborhood').value,city:$('detailCity').value,state:$('detailState').value,zip:$('detailZip').value}};try{const d=await authPost('/api/order/details',b);dismissModal();toast(d.message||'Detalhes enviados à loja.')}catch(e){toast(e.message)}}},0)}const paymentQuery=new URLSearchParams(window.location.search),paymentResult=paymentQuery.get('payment'),returnedPayment=paymentQuery.get('payment_id');if(returnedPayment)fetch('/api/payment/status?id='+encodeURIComponent(returnedPayment)).then(r=>r.json()).then(d=>{if(d.ok)toast('Status do pagamento: '+d.label)}).catch(()=>{});if(paymentResult==='success'){setTimeout(()=>toast('Pagamento aprovado! Pedido recebido pela loja.'),700);setTimeout(showDetailsForm,850)}if(paymentResult==='pending'){setTimeout(()=>toast('Pagamento em análise. Envie os detalhes para a loja.'),700);setTimeout(showDetailsForm,850)}if(paymentResult==='failure')setTimeout(()=>toast('Pagamento não concluído. Você pode tentar novamente.'),700);if(new URLSearchParams(window.location.search).has('loja')){view('store')}'''
    return {'index.html':html_doc,'styles.css':css,'app.js':js,'forgeai.json':json.dumps({'name':'VendaCertaAI','type':'VendaCertaAI navegável'},ensure_ascii=False)}


def create_project(name, prompt):
    kind, theme = infer_type(prompt)
    if 'vendacertaai' in prompt.lower():
        kind, theme, ai = 'VendaCertaAI navegável', 'purple', {'files':vendacerta_files(),'type':'VendaCertaAI navegável','name':'VendaCertaAI'}
    else:
        ai = gemini_files(prompt, name)
    if ai:
        kind = ai['type']
        name = ai.get('name', name) or name
    
    project_id = slugify(name) + '-' + str(int(time.time()))
    folder = DATA / project_id
    folder.mkdir(parents=True, exist_ok=True)
    files = ai['files'] if ai else app_files(name, prompt, kind)
    for filename, content in files.items():
        (folder / filename).write_text(content, encoding='utf-8')
    archive = DATA / f'{project_id}.tar.gz'
    with tarfile.open(archive, 'w:gz') as tar:
        tar.add(folder, arcname=slugify(name))
    return {'id':project_id,'name':name,'type':kind,'theme':theme,'status':'Rascunho','download':f'/download/{project_id}.tar.gz','files':files}


def vendacerta_page():
    files=vendacerta_files()
    page=files['index.html']
    # Production-safe foundation panel is injected without altering existing storefront flows.
    panel='''<section id="foundationPanel" class="view" aria-labelledby="foundationTitle"><div class="section-heading"><small>CONFIANÇA E CONTROLE</small><h2 id="foundationTitle">Sua loja pronta</h2><p class="muted">Orientações e métricas reais para começar. Nada aqui inventa vendas ou automatiza descontos.</p></div><div id="onboardingCard" class="product"><div id="onboardingContent">Carregando checklist…</div></div><div id="metricsCard" class="product"><h3>Métricas da loja</h3><div id="metricsContent">Carregando dados persistidos…</div></div><div id="pilotCard" class="product"><h3>Piloto seguro da IA</h3><p class="muted">A IA está em modo sugestão: recomendações exigem sua aprovação e nunca aplicam descontos sozinhas.</p><p id="pilotState"><strong>Estado:</strong> sugestões somente · aprovação humana necessária</p></div><div id="privacyCard" class="product"><h3>Privacidade e confiança</h3><div id="privacyContent">Carregando informações…</div></div><div id="auditCard" class="product"><h3>Histórico de auditoria</h3><p class="muted">Eventos da sua loja, sem conteúdo emocional sensível.</p><div id="auditContent">Carregando…</div></div></section>'''
    page=page.replace('</main>',panel+'</main>')
    page=page.replace('<link rel="stylesheet" href="styles.css">','<style>'+files['styles.css']+'#foundationPanel{padding:28px 5%;max-width:900px;margin:auto}.view:not(.active){display:none}.product{padding:20px;margin:12px 0;border-radius:18px}.muted{color:#64748b}.check{padding:8px 0}.check.done{text-decoration:line-through;opacity:.7}@media(max-width:700px){#foundationPanel{padding:20px 16px}}button,input,select{font-size:16px}button:focus-visible,input:focus-visible,select:focus-visible{outline:3px solid #06b6d4;outline-offset:2px}</style>')
    gallery_css='''<style>
.gallery-picker{display:grid;gap:8px;padding:12px;border:1px dashed #94a3b8;border-radius:12px}.gallery-preview{display:flex;gap:8px;overflow-x:auto}.gallery-preview img{width:68px;height:68px;object-fit:cover;border-radius:9px}.gallery-preview button{font-size:11px}.detail-gallery{position:relative}.detail-gallery img{width:100%;max-height:55vh;object-fit:contain;border-radius:14px}.detail-gallery .gallery-nav{display:flex;justify-content:space-between;margin-top:8px}.speech-control{margin-left:8px;padding:5px 9px;border-radius:8px;font-size:11px}.video-feed{display:flex!important;overflow-x:auto!important;scroll-snap-type:x mandatory!important;gap:14px!important}.video-card{flex:0 0 min(92vw,430px)!important;min-height:70vh!important;height:calc(100svh - 150px)!important;scroll-snap-align:center!important}.video-card video{height:100%!important;object-fit:cover!important}.video-overlay{padding:80px 18px 20px!important}.negotiation-note{padding:10px;border:1px solid #fbbf24;border-radius:10px;color:#92400e;font-size:12px;margin-top:10px}.customer-storefront header{position:sticky;top:0;z-index:10}.customer-storefront header nav{display:flex;align-items:center;gap:8px;flex:1}.customer-storefront header nav button{display:inline-flex}.customer-storefront .customer-tab{border:0;background:transparent;color:#64748b;padding:10px 14px;border-radius:999px;font-weight:700}.customer-storefront .customer-tab.active{background:#e0f2fe;color:#0369a1}.customer-storefront .customer-chat{margin-left:auto}.customer-storefront main{padding-top:8px}.customer-storefront #videos .video-admin{display:none!important}.customer-storefront #assistant .agent-controls{display:none!important}.customer-storefront #assistant{max-width:760px;margin:auto}.customer-storefront .product-art{cursor:pointer}.customer-storefront .beta-banner{display:none}
/* Customer links are safe before any asynchronous auth request completes. */
html.customer-request header nav,html.customer-request #headerLogin,html.customer-request #accountBadge,html.customer-request #themeBtn,html.customer-request #backBtn,html.customer-request #trialBtn,html.customer-request #loginBtn,html.customer-request #trialHelp,html.customer-request #planAccessBtn,html.customer-request #admBtn{display:none!important}
html.customer-request header nav{display:none!important}
html.customer-request #home .actions{display:none!important}
html.customer-request:not(.customer-entered) main .view:not(#store){display:none!important}
html.customer-request.customer-ready main .view#productsView{display:block!important}
/* A public customer link opens the actual catalog view, not the entrepreneur landing shell. */
html.customer-request main .view#productsView{display:block!important;visibility:visible!important;opacity:1!important}
html.customer-request #store,html.customer-request #home,html.customer-request #videos,html.customer-request #assistant{display:none!important}
html.customer-request #productsView .section-head .actions,html.customer-request #productsView #productsAdmin{display:none!important}
html.customer-request body #productsView{display:block!important;visibility:visible!important;opacity:1!important}
html.customer-request body #productsView #products{display:grid!important;visibility:visible!important}
html.customer-request #backBtn{display:none!important}
/* The entrepreneur landing screen is intentionally limited to its two entry actions.
   Keep platform navigation out of the DOM's visible UI until session resolution succeeds. */
html.vc-auth-loading header nav,html.vc-anonymous header nav,html.vc-auth-loading #headerLogin,html.vc-anonymous #headerLogin,html.vc-auth-loading #accountBadge,html.vc-anonymous #accountBadge,html.vc-auth-loading #themeBtn,html.vc-anonymous #themeBtn,html.vc-auth-loading #backBtn,html.vc-anonymous #backBtn{display:none!important}
html.vc-auth-loading header nav,html.vc-anonymous header nav{visibility:hidden}
@media(max-width:700px){.customer-storefront header{padding:12px 16px}.customer-storefront header nav{overflow-x:auto;white-space:nowrap}.customer-storefront .customer-chat{margin-left:0}.customer-storefront header .logo{font-size:16px}.customer-storefront .customer-tab{padding:9px 11px}}</style>'''
    page=page.replace('</head>',gallery_css+"<script>(function(){var q=new URLSearchParams(location.search);document.documentElement.classList.add('vc-auth-loading');if(q.has('loja')&&( !q.has('cliente')||q.get('cliente')==='1'))document.documentElement.classList.add('customer-request')})();</script></head>")
    extra_js='''async function loadFoundation(){if(!authUser)return;const q='?loja='+encodeURIComponent(storeSlug);try{const [o,m,p,a]=await Promise.all([fetch('/api/onboarding'+q),fetch('/api/metrics'+q),fetch('/api/privacy'+q),fetch('/api/audit'+q)]);const od=await o.json(),md=await m.json(),pd=await p.json(),ad=await a.json();if(od.checklist){$('onboardingContent').innerHTML=od.checklist.checks.map(c=>'<div class="check '+(c.done?'done':'')+'" role="listitem">'+(c.done?'✓ ':'○ ')+safeText(c.label)+'</div>').join('')+'<p><strong>'+od.checklist.completed+'/'+od.checklist.total+'</strong> concluídos · '+(od.checklist.ready?'Loja pronta para começar.':'Complete os itens essenciais.')+'</p>'}if(md.metrics){const x=md.metrics;$('metricsContent').innerHTML='<p>Produtos: <strong>'+x.catalog_products+'</strong> · Pedidos: <strong>'+x.orders_total+'</strong> · Pagos: <strong>'+x.orders_paid+'</strong></p><p>Vendas pagas: <strong>R$ '+Number(x.gross_paid||0).toFixed(2).replace('.',',')+'</strong></p><p class="muted">'+safeText(x.note)+' '+(x.data_sufficiency==='insufficient'?'Ainda há poucos dados para conclusões confiáveis.':'Base inicial; não é previsão.')+'</p>'}if(pd.privacy){const x=pd.privacy;$('privacyContent').innerHTML='<p>'+safeText(x.ai_profile)+'</p><p>'+safeText(x.data_use)+'</p><p>'+safeText(x.memory)+'</p><p>'+safeText(x.controls)+'</p><p><strong>Exclusão/exportação:</strong> '+safeText(x.deletion)+'</p>'}if(ad.events){$('auditContent').innerHTML=ad.events.length?ad.events.slice(-12).reverse().map(e=>'<div class="check"><strong>'+safeText(e.event)+'</strong> · '+new Date(Number(e.at)*1000).toLocaleString('pt-BR')+'</div>').join(''):'<p class="muted">Nenhum evento registrado ainda.</p>'}}catch(e){toast('Não foi possível carregar o painel de confiança agora.')}}const _setAuth=setAuth;setAuth=function(u){_setAuth(u);document.documentElement.classList.toggle('vc-authenticated',!!u);document.documentElement.classList.toggle('vc-anonymous',!u);document.documentElement.classList.remove('vc-auth-loading');if(u){loadFoundation()}};document.querySelectorAll('[data-view]').forEach(b=>{if(b.dataset.view==='foundationPanel')b.onclick=()=>{view('foundationPanel');loadFoundation()}});const foundationButton=document.createElement('button');foundationButton.className='outline';foundationButton.textContent='Loja pronta · confiança';foundationButton.setAttribute('aria-label','Abrir checklist, métricas e privacidade');foundationButton.onclick=()=>{if(!authUser){openLogin();return}view('foundationPanel');loadFoundation()};document.querySelector('header,nav')?.appendChild(foundationButton);
// UX phase: bounded multi-image gallery, accessible product detail and speech controls.
function galleryImages(p){let a=Array.isArray(p.images)?p.images.slice(0,6):[];if(!a.length&&p.image)a=[p.image];return a.filter(Boolean)}
function galleryPicker(existing){let imgs=galleryImages(existing||{});return '<label class="gallery-picker">Galeria de imagens (até 6)<input id="galleryFiles" type="file" accept="image/jpeg,image/png,image/webp,image/gif" multiple><span>Selecione várias fotos pelo celular; serão comprimidas antes de salvar.</span><div id="galleryPreview" class="gallery-preview">'+imgs.map((x,i)=>'<span><img src="'+safeAttr(x)+'" alt="Imagem '+(i+1)+'"><button type="button" data-remove-gallery="'+i+'">Remover</button></span>').join('')+'</div></label>'}
async function prepareGallery(existing){let out=galleryImages(existing||{});const input=$('galleryFiles');if(input&&input.files.length){if(input.files.length+out.length>6)throw new Error('A galeria aceita até 6 imagens.');for(const f of input.files)out.push(await compressProductImage(f))}return out.slice(0,6)}
function openProductDetails(p){let imgs=galleryImages(p),idx=0;modal('<small>DETALHES DO PRODUTO</small><h2>'+safeText(p.name)+'</h2><div class="detail-gallery"><img id="detailGalleryImage" src="'+safeAttr(imgs[0]||'')+'" alt="'+safeAttr(p.name)+'"><div class="gallery-nav"><button class="outline" id="galleryPrev" aria-label="Imagem anterior">←</button><span id="galleryCount">'+(imgs.length?'1 / '+imgs.length:'Sem imagem')+'</span><button class="outline" id="galleryNext" aria-label="Próxima imagem">→</button></div></div><p class="muted">'+safeText(p.desc)+'</p><div class="price">'+safeText(p.price)+'</div><button class="primary" id="detailBuy">Comprar via Pix rápido</button>');function show(){if(!imgs.length)return;$('detailGalleryImage').src=imgs[idx];$('galleryCount').textContent=(idx+1)+' / '+imgs.length}$('galleryPrev').onclick=()=>{idx=(idx+imgs.length-1)%imgs.length;show()};$('galleryNext').onclick=()=>{idx=(idx+1)%imgs.length;show()};$('detailBuy').onclick=()=>{dismissModal();document.querySelector('[data-buy="'+products.indexOf(p)+'"]')?.click()}}
const _renderProducts=renderProducts;renderProducts=function(){_renderProducts();document.querySelectorAll('#products .product,#productsAdmin .product').forEach((card,i)=>card.querySelector('.product-art')?.addEventListener('click',()=>openProductDetails(products[i])));bindGalleryEditors()};
function openGalleryEdit(i){let p=products[i],images=galleryImages(p);modal('<small>EDITAR PRODUTO</small><h2>Atualizar produto</h2>'+galleryPicker(p)+'<input id="editName" placeholder="Nome do produto"><input id="editDesc" placeholder="Descrição"><input id="editPrice" placeholder="Preço"><button class="primary" id="updateProduct">Salvar alterações</button>');$('editName').value=p.name;$('editDesc').value=p.desc;$('editPrice').value=p.price;$('updateProduct').onclick=async()=>{try{let next=await prepareGallery(p);let name=$('editName').value.trim(),price=$('editPrice').value.trim();if(!name||!price)throw new Error('Informe nome e preço');products[i]={...p,name,desc:$('editDesc').value.trim()||'Produto disponível na loja.',price,images:next,image:next[0]||''};await syncProducts();renderProducts();dismissModal();toast('Produto atualizado!')}catch(e){toast(e.message)}}}
function bindGalleryEditors(){document.querySelectorAll('#productsAdmin [data-edit]').forEach(b=>{b.onclick=()=>openGalleryEdit(Number(b.dataset.edit))})}
const _addProduct=$('addProduct').onclick;$('addProduct').onclick=()=>{modal('<small>NOVO PRODUTO</small><h2>Adicionar produto</h2>'+galleryPicker()+'<input id="productName" placeholder="Nome do produto"><input id="productDesc" placeholder="Descrição"><input id="productPrice" placeholder="Preço"><button class="primary" id="saveProduct">Salvar produto</button>');$('saveProduct').onclick=async()=>{try{let images=await prepareGallery({});let name=$('productName').value.trim(),desc=$('productDesc').value.trim(),price=$('productPrice').value.trim();if(!name||!price)throw new Error('Informe nome e preço');products.push({name,desc:desc||'Produto disponível na loja.',price,images,image:images[0]});await syncProducts();renderProducts();dismissModal();toast('Produto salvo com galeria!')}catch(e){toast(e.message)}}};
const oldChatForm=$('chatForm');new MutationObserver(()=>{document.querySelectorAll('.bubble.ai').forEach(b=>{if(!b.querySelector('.speech-control')&&b.id!=='typing'){let btn=document.createElement('button');btn.className='speech-control outline';btn.textContent='🔊 Ouvir resposta';btn.onclick=()=>{if(speechSynthesis.speaking){speechSynthesis.cancel();btn.textContent='🔊 Ouvir resposta'}else{speechSynthesis.cancel();let u=new SpeechSynthesisUtterance(b.textContent);u.lang='pt-BR';u.rate=.98;u.onend=()=>btn.textContent='🔊 Ouvir resposta';speechSynthesis.speak(u);btn.textContent='■ Parar'}};b.appendChild(btn)}}).observe($('chat'),{childList:true,subtree:true});
(function negotiation(){if(!authUser)return;let c=$('agentControls');if(c&&!$('negotiationNotice')){let n=document.createElement('div');n.id='negotiationNotice';n.className='negotiation-note';n.innerHTML='<strong>Negociação Relâmpago · somente sugestão</strong><br>O limite de desconto acima só orienta a Consultora. Nenhum desconto é aplicado automaticamente; sem cronômetro ou urgência falsa.';c.appendChild(n)}})();
// Dedicated customer storefront. Resolve auth before enabling public mode.
// Customer links open directly on the store; entrepreneur controls remain untouched
// for authenticated sessions and are never exposed in the public customer DOM.
(function customerStorefront(){
  const params=new URLSearchParams(window.location.search);
  const requested=params.has('loja') && (!params.has('cliente') || params.get('cliente')==='1');
  if(!requested)return;
  document.documentElement.classList.add('customer-request');
  let customerMode=false, authResolved=false;
  const el=id=>document.getElementById(id);
  function active(){return requested&&authResolved&&!window.authUser&&!authUser;}
  function enterCustomer(){
    if(!active()||customerMode)return;
    customerMode=true;
    document.body.classList.add('customer-storefront');
    document.documentElement.classList.add('customer-ready','customer-entered');
    document.querySelector('.beta-banner')?.remove();
    ['#headerLogin','#accountBadge','#themeBtn','#trialBtn','#loginBtn','#trialHelp','#planAccessBtn','#admBtn','#videoAdmin','#foundationPanel','#backBtn'].forEach(s=>document.querySelector(s)?.remove());
    const nav=document.querySelector('header nav'); if(nav){nav.innerHTML='';nav.style.display='none';}
    const home=el('home'); if(home)home.classList.remove('active');
    document.querySelector('#home .actions')?.remove();
    document.querySelector('#store .section-head .actions')?.remove();
    document.querySelector('#videos .section-head .actions')?.remove();
    const title=document.querySelector('#store h2'); if(title)title.textContent='Produtos da loja';
    const copy=document.querySelector('#store p'); if(copy)copy.textContent='Escolha um produto ou fale com a vendedora.';
    if(typeof view==='function')view('productsView',false);
    if(typeof loadProducts==='function')loadProducts();
    if(typeof loadVideos==='function')loadVideos();
  }
  // This gate prevents an async auth response from briefly showing customer UI to an entrepreneur.
  fetch('/api/auth/me',{credentials:'same-origin',cache:'no-store'}).then(r=>r.json()).then(d=>{
    authResolved=true;
    if(!d.ok&&!authUser)enterCustomer();
  }).catch(()=>{authResolved=true;if(!authUser)enterCustomer();});
})();'''
    page=page.replace('<script src="app.js"></script>','<script>'+files['app.js'].replace('</script>','<\\/script>')+extra_js+'</script>')
    return page.encode('utf-8')


def access_codes():
    data=load_json(ACCESS_CODE_FILE,[])
    return data if isinstance(data,list) else []


def save_access_codes(codes):
    atomic_save(ACCESS_CODE_FILE,codes[-2000:])


def code_hash(code):
    return hashlib.sha256(str(code).strip().upper().encode()).hexdigest()


def public_code(record):
    return {k:record.get(k) for k in ('plan','days','status','created_at','redeemed_at','redeemed_by')} | {'code':record.get('code') if record.get('status')=='available' else None}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def end_json(self, data, status=200, cookie=None, clear_cookie=False, admin_cookie=None):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.send_header('Cache-Control','no-store')
        if cookie: set_session(self,cookie)
        if admin_cookie: set_session(self,admin_cookie,admin=True)
        if clear_cookie: self.send_header('Set-Cookie','vc_session=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0')
        self.end_headers(); self.wfile.write(raw)

    def same_origin(self):
        origin=self.headers.get('Origin','').strip()
        if not origin: return True
        allowed={PUBLIC_BASE, 'http://localhost:8000', 'http://127.0.0.1:8000'}
        return origin in allowed

    def admin_authenticated(self):
        token=''
        for part in self.headers.get('Cookie','').split(';'):
            if part.strip().startswith('vc_admin='): token=part.strip().split('=',1)[1]
        data=auth_data(); entry=data.get('admin_sessions',{}).get(token)
        return bool(isinstance(entry,dict) and time.time()-float(entry.get('created_at',0) or 0)<=SESSION_TTL)
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/vendacertaai','/vendacertaai/'):
            raw=vendacerta_page(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path=='/api/health':
            storage=storage_status()
            return self.end_json({'ok':True,'service':'ForgeAI','storage':storage,'readiness':'durable' if storage['durable'] else 'beta'})
        if path=='/api/auth/me':
            user=session_user(self)
            return self.end_json({'ok':bool(user),'user':({k:user.get(k) for k in ('id','name','business','slug','trial_ends_at')} if user else None)})
        if path=='/api/access/status':
            current=session_user(self)
            if not current: return self.end_json({'ok':False,'locked':True,'user':None})
            return self.end_json({'ok':True,'user':{k:current.get(k) for k in ('name','business','slug')},'access':access_status(current)})
        if path=='/api/store':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0].strip() or 'vendacertaai'
            if slug != 'vendacertaai' and not user_by_slug(slug):
                return self.end_json({'error':'Loja não encontrada.'},404)
            return self.end_json({'ok':True,'slug':slug,'products':store_products(slug)})
        if path=='/api/videos':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0].strip() or 'vendacertaai'
            if slug != 'vendacertaai' and not user_by_slug(slug): return self.end_json({'error':'Loja não encontrada.'},404)
            # Public response intentionally exposes only published clips for this tenant.
            return self.end_json({'ok':True,'slug':slug,'videos':store_videos(slug)})
        if path=='/api/orders':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0]
            current=session_user(self)
            if not current or current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
            if access_status(current)['locked']: return self.end_json({'error':'Seu teste terminou. Ative um plano para continuar usando a gestão da loja.'},402)
            return self.end_json({'ok':True,'slug':slug,'orders':store_orders(slug)})
        if path=='/api/agent/settings':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0]
            current=session_user(self)
            if not current or current.get('slug') != slug: return self.end_json({'error':'Faça login para acessar as regras da IA.'},401)
            return self.end_json({'ok':True,'slug':slug,'settings':agent_settings(slug)})
        if path=='/api/payment/status':
            payment_id=parse_qs(urlparse(self.path).query).get('id',[''])[0].strip()
            if not payment_id or len(payment_id)>100: return self.end_json({'error':'Pagamento não encontrado.'},404)
            known=find_payment_record(payment_id)
            payment=mp_payment(payment_id)
            if not payment and not known: return self.end_json({'error':'Pagamento não encontrado.'},404)
            if payment:
                apply_payment_update(payment)
                return self.end_json({'ok':True,'payment_id':payment.get('id'),'status':payment.get('status'),'label':payment_label(payment.get('status'))})
            return self.end_json({'ok':True,'payment_id':payment_id,'status':known.get('status','pending'),'label':payment_label(known.get('status','pending'))})
        if path=='/api/payment/history':
            slug=parse_qs(urlparse(self.path).query).get('loja',[''])[0].strip(); current=session_user(self)
            if not current or current.get('slug') != slug:
                return self.end_json({'error':'Faça login para acessar o histórico de pagamentos.'},401)
            return self.end_json({'ok':True,'slug':slug,'payments':tenant_payment_history(slug,current.get('email',''))})
        if path=='/api/audit':
            slug=parse_qs(urlparse(self.path).query).get('loja',[''])[0].strip(); current=session_user(self)
            if not current or current.get('slug') != slug:
                return self.end_json({'error':'Acesso não autorizado ao histórico de auditoria.'},401)
            return self.end_json({'ok':True,'slug':slug,'events':tenant_audit_events(slug)})
        if path in ('/api/onboarding','/api/metrics','/api/privacy'):
            slug=parse_qs(urlparse(self.path).query).get('loja',[''])[0].strip(); current=session_user(self)
            if not current or current.get('slug') != slug:
                return self.end_json({'error':'Acesso não autorizado a esta loja.'},401)
            if path=='/api/onboarding': return self.end_json({'ok':True,'slug':slug,'checklist':onboarding_checklist(slug)})
            if path=='/api/metrics': return self.end_json({'ok':True,'slug':slug,'metrics':tenant_metrics(slug)})
            return self.end_json({'ok':True,'slug':slug,'privacy':privacy_trust(slug)})
        if path=='/api/marketplace/summary':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0]; current=session_user(self)
            if not current or current.get('slug') != slug: return self.end_json({'error':'Faça login para acessar o resumo financeiro.'},401)
            return self.end_json({'ok':True,'slug':slug,'summary':marketplace_summary(slug)})
        if path.startswith('/download/'):
            file=DATA / Path(path).name
            if file.exists():
                raw=file.read_bytes(); self.send_response(200); self.send_header('Content-Type','application/gzip'); self.send_header('Content-Disposition',f'attachment; filename="{file.name}"'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            return self.end_json({'error':'Projeto não encontrado'},404)
        return super().do_GET()
    def do_POST(self):
        route=urlparse(self.path).path
        if route not in ('/api/generate','/api/modify','/api/seller-chat','/api/store','/api/orders','/api/order/create','/api/payment/create','/api/order/details','/api/access/checkout','/api/payment/webhook','/api/marketplace/summary','/api/customer/profile','/api/agent/settings','/api/auth/signup','/api/auth/login','/api/auth/logout','/api/admin/login','/api/admin/codes','/api/admin/codes/create','/api/access/redeem','/api/videos'): return self.end_json({'error':'Rota não encontrada'},404)
        try:
            if not self.same_origin() and route != '/api/payment/webhook': return self.end_json({'error':'Origem não autorizada.'},403)
            length=int(self.headers.get('Content-Length','0'))
            if length > MAX_BODY_BYTES: return self.end_json({'error':'Requisição muito grande.'},413)
            body=json.loads(self.rfile.read(length) or '{}')
            if route == '/api/auth/signup':
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password','')); name=str(body.get('name','')).strip(); business=str(body.get('business','')).strip()
                if '@' not in email or len(password)<6 or not name or not business: return self.end_json({'error':'Preencha nome, negócio, e-mail e senha com pelo menos 6 caracteres.'},400)
                data=auth_data()
                if email in data['users']: return self.end_json({'error':'Este e-mail já está cadastrado.'},409)
                slug=slugify(business); used={u.get('slug') for u in data['users'].values()}; base=slug; n=2
                while slug in used: slug=f'{base}-{n}'; n+=1
                salt,digest=password_hash(password); now=time.time(); user={'id':secrets.token_hex(12),'email':email,'name':name,'business':business,'slug':slug,'salt':salt,'password_hash':digest,'trial_ends_at':now+48*3600,'access_plan':None,'access_until':0,'plan_status':'trial'}
                data['users'][email]=user; token=secrets.token_urlsafe(32); data['sessions'][token]={'email':email,'created_at':time.time()}; save_auth(data); save_store_products(slug,DEFAULT_PRODUCTS.copy()); save_store_orders(slug,[]); save_agent_settings(slug,DEFAULT_AGENT_SETTINGS.copy())
                return self.end_json({'ok':True,'user':{k:user[k] for k in ('id','name','business','slug','trial_ends_at')}},cookie=token)
            if route == '/api/auth/login':
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password','')); data=auth_data(); user=data['users'].get(email)
                if not user: return self.end_json({'error':'E-mail ou senha inválidos.'},401)
                _,digest=password_hash(password,user.get('salt'))
                if not secrets.compare_digest(digest,user.get('password_hash','')): return self.end_json({'error':'E-mail ou senha inválidos.'},401)
                token=secrets.token_urlsafe(32); data['sessions'][token]={'email':email,'created_at':time.time()}; save_auth(data)
                return self.end_json({'ok':True,'user':{k:user[k] for k in ('id','name','business','slug','trial_ends_at')}},cookie=token)
            if route == '/api/auth/logout':
                token=''
                for part in self.headers.get('Cookie','').split(';'):
                    if part.strip().startswith('vc_session='): token=part.strip().split('=',1)[1]
                data=auth_data(); data['sessions'].pop(token,None); save_auth(data)
                return self.end_json({'ok':True},clear_cookie=True)
            if route == '/api/admin/login':
                configured=os.environ.get('ADMIN_PASSWORD','').strip()
                password=str(body.get('password',''))
                if not configured: return self.end_json({'error':'Central ADM desativada: configure ADMIN_PASSWORD no ambiente seguro.'},503)
                if not hmac.compare_digest(password,configured): return self.end_json({'error':'Senha incorreta.'},401)
                token=secrets.token_urlsafe(32); data=auth_data(); data['admin_sessions'][token]={'created_at':time.time()}; save_auth(data)
                return self.end_json({'ok':True},admin_cookie=token)
            if route in ('/api/admin/codes','/api/admin/codes/create'):
                if not self.admin_authenticated(): return self.end_json({'error':'Sessão ADM inválida.'},401)
                codes=access_codes()
                if route == '/api/admin/codes': return self.end_json({'ok':True,'codes':[public_code(c) for c in codes]})
                plan=str(body.get('plan','')).upper().strip()
                if plan not in PLAN_DAYS: return self.end_json({'error':'Plano inválido.'},400)
                code=plan+'-'+secrets.token_hex(5).upper(); record={'hash':code_hash(code),'code':code,'plan':plan,'days':PLAN_DAYS[plan],'status':'available','created_at':time.time()}; codes.append(record); save_access_codes(codes); audit_event('platform','access_code_created',{'plan':plan}); return self.end_json({'ok':True,'code':code,'plan':plan,'days':PLAN_DAYS[plan]})
            if route == '/api/access/redeem':
                current=session_user(self)
                if not current: return self.end_json({'error':'Entre na sua conta para usar um código.'},401)
                raw=str(body.get('code','')).strip().upper()
                if not raw or len(raw)>80: return self.end_json({'error':'Código inválido.'},400)
                codes=access_codes(); record=next((c for c in codes if c.get('hash')==code_hash(raw) and c.get('status')=='available'),None)
                if not record: return self.end_json({'error':'Código inválido, já utilizado ou expirado.'},400)
                now=time.time(); data=auth_data(); user=data['users'].get(current.get('email',''))
                start=max(now,float(user.get('access_until',0) or 0)); user['access_plan']=record['plan']; user['access_until']=start+int(record['days'])*86400; user['plan_status']='active'; save_auth(data)
                record.update({'status':'redeemed','redeemed_at':now,'redeemed_by':user.get('id')}); save_access_codes(codes); audit_event(user.get('slug',''),'access_code_redeemed',{'plan':record['plan']}); return self.end_json({'ok':True,'access':access_status(user)})
            if route == '/api/access/checkout':
                current=session_user(self)
                if not current: return self.end_json({'error':'Entre na sua conta para escolher um plano.'},401)
                plan=str(body.get('plan','')).upper().strip()
                if plan not in PLAN_PRICES: return self.end_json({'error':'Plano inválido.'},400)
                preference,error=create_plan_preference(current.get('email',''),plan)
                if error: return self.end_json({'error':error},503)
                checkout_url=preference.get('init_point') or preference.get('sandbox_init_point')
                if not checkout_url: return self.end_json({'error':'O Mercado Pago não retornou um link de checkout válido.'},503)
                reference=str(preference.get('external_reference',''))
                save_payment_record({'reference':reference,'preference_id':str(preference.get('id','')),'email':current.get('email',''),'plan':plan,'amount':PLAN_PRICES[plan],'days':PLAN_DAYS[plan],'status':'created','created_at':time.time(),'checkout_url':checkout_url})
                audit_event(current.get('slug',''),'plan_checkout_created',{'plan':plan,'preference_id':str(preference.get('id',''))})
                return self.end_json({'ok':True,'plan':plan,'price':PLAN_PRICES[plan],'days':PLAN_DAYS[plan],'checkout_url':checkout_url,'preference_id':preference.get('id')})
            if route == '/api/customer/profile':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; session_id=str(body.get('session_id','')).strip()[:100]; name=str(body.get('name','')).strip()[:100]; phone=str(body.get('phone','')).strip()[:30]
                if not session_id or not name or not phone: return self.end_json({'error':'Informe nome e telefone.'},400)
                previous=customer_profile(slug,session_id); profile={'name':name,'phone':phone,'updated_at':time.time(),'orders':previous.get('orders',[])}; save_customer_profile(slug,session_id,profile)
                return self.end_json({'ok':True,'profile':profile})
            if route == '/api/payment/webhook':
                if not verify_mp_webhook(self,body): return self.end_json({'error':'Notificação não autenticada.'},401)
                notification_type=str(body.get('type') or body.get('topic') or '').lower(); payment_id=(body.get('data',{}).get('id') if isinstance(body.get('data',{}),dict) else body.get('id'))
                if notification_type == 'payment' and payment_id:
                    payment=mp_payment(payment_id)
                    if payment: apply_payment_update(payment)
                return self.end_json({'ok':True})
            if route == '/api/order/details':
                order_id=str(body.get('order_id','')).strip()[:80]
                token=str(body.get('details_token','')).strip()[:160]
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'
                if not order_id or not token or len(token) < 24: return self.end_json({'error':'Link de detalhes inválido ou expirado.'},400)
                address,error=clean_address(body.get('address',body))
                if error: return self.end_json({'error':error},400)
                orders=store_orders(slug); target=next((o for o in orders if str(o.get('id'))==order_id),None)
                if not target or str(target.get('slug',slug)) != slug or not hmac.compare_digest(str(target.get('details_token_hash','')),details_token_hash(token)):
                    return self.end_json({'error':'Link de detalhes inválido ou expirado.'},403)
                if str(target.get('status','')).lower() in ('pagamento cancelado','pagamento recusado','pagamento estornado'):
                    return self.end_json({'error':'Este pedido não pode mais receber detalhes.'},409)
                target['delivery_details']=address; target['details_updated_at']=time.time(); save_store_orders(slug,orders)
                audit_event(slug,'order_details_completed',{'order_id':order_id})
                return self.end_json({'ok':True,'order':public_order(target),'message':'Detalhes recebidos. A loja poderá preparar o pedido após a confirmação do pagamento.'})
            if route == '/api/payment/create':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'
                if not store_access_active(slug): return self.end_json({'error':'O período de teste terminou. Ative um plano para continuar vendendo.'},402)
                order=body.get('order',{}); session_id=str(body.get('session_id','')).strip()[:100]; customer=body.get('customer',{}) if isinstance(body.get('customer',{}),dict) else {}
                if not isinstance(order,dict): return self.end_json({'error':'Pedido inválido.'},400)
                product=catalog_product(slug,order)
                if not product: return self.end_json({'error':'Produto não encontrado no catálogo atual.'},409)
                customer_name=str(customer.get('name','')).strip()[:100]; customer_phone=str(customer.get('phone','')).strip()[:30]
                if not customer_name or not customer_phone: return self.end_json({'error':'Informe nome e telefone.'},400)
                order_id=str(order.get('id') or secrets.token_hex(10))[:80]
                orders=store_orders(slug)
                existing=next((o for o in orders if str(o.get('id'))==order_id),None)
                if existing and existing.get('checkout_url'): return self.end_json({'ok':True,'order':existing,'checkout_url':existing['checkout_url'],'payment_provider':'mercadopago','idempotent':True})
                amount=price_number(product.get('price','')); commission_amount=round(amount*PLATFORM_COMMISSION_RATE,2); seller_amount=round(amount-commission_amount,2)
                details_token=secrets.token_urlsafe(32)
                preference,error=create_mp_preference(product.get('name'),amount,order_id,slug,details_token)
                if error: return self.end_json({'error':error},503)
                checkout_url=preference.get('init_point') or preference.get('sandbox_init_point')
                if not checkout_url: return self.end_json({'error':'O Mercado Pago não retornou um link de checkout válido.'},503)
                saved={'id':order_id,'slug':slug,'product':product.get('name'),'product_id':product.get('id'),'price':product.get('price'),'customer_name':customer_name,'customer_phone':customer_phone,'session_id':session_id,'status':'Pagamento pendente','payment_preference_id':preference.get('id'),'checkout_url':checkout_url,'commission_rate':PLATFORM_COMMISSION_RATE,'commission_amount':commission_amount,'seller_amount_estimate':seller_amount,'settlement_status':'Aguardando conexão OAuth do vendedor','details_token_hash':details_token_hash(details_token),'details_status':'Pendente','created_at':time.time()}
                if session_id:
                    previous=customer_profile(slug,session_id); profile={'name':customer_name,'phone':customer_phone,'updated_at':time.time(),'orders':previous.get('orders',[])+[product.get('name')]}; save_customer_profile(slug,session_id,profile)
                orders.append(saved); save_store_orders(slug,orders); audit_event(slug,'order_payment_created',{'order_id':order_id,'preference_id':str(preference.get('id',''))})
                return self.end_json({'ok':True,'order':public_order(saved),'checkout_url':checkout_url,'payment_provider':'mercadopago','details_token':details_token,'details_url':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=success&order_id='+quote(order_id)+'&details_token='+quote(details_token),'checkout_label':'Pix rápido via checkout'})
            if route == '/api/order/create':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'
                if not store_access_active(slug): return self.end_json({'error':'O período de teste terminou. A loja precisa ativar um plano para aceitar novos pedidos.'},402)
                order=body.get('order',{}); session_id=str(body.get('session_id','')).strip()[:100]; customer=body.get('customer',{}) if isinstance(body.get('customer',{}),dict) else {}
                if not isinstance(order,dict): return self.end_json({'error':'Pedido inválido.'},400)
                product=catalog_product(slug,order)
                customer_name=str(customer.get('name','')).strip()[:100]; customer_phone=str(customer.get('phone','')).strip()[:30]
                if not product or not customer_name or not customer_phone: return self.end_json({'error':'Produto, nome e telefone são obrigatórios.'},400)
                order_id=str(order.get('id') or secrets.token_hex(10))[:80]; orders=store_orders(slug)
                if any(str(o.get('id'))==order_id for o in orders): return self.end_json({'ok':True,'order':next(o for o in orders if str(o.get('id'))==order_id),'idempotent':True})
                saved={'id':order_id,'product':product.get('name'),'product_id':product.get('id'),'price':product.get('price'),'customer_name':customer_name,'customer_phone':customer_phone,'session_id':session_id,'status':'Novo','created_at':time.time()}
                if session_id:
                    previous=customer_profile(slug,session_id); profile={'name':customer_name,'phone':customer_phone,'updated_at':time.time(),'orders':previous.get('orders',[])+[product.get('name')]}; save_customer_profile(slug,session_id,profile)
                orders.append(saved); save_store_orders(slug,orders); audit_event(slug,'order_created',{'order_id':order_id})
                return self.end_json({'ok':True,'order':saved})
            if route == '/api/videos':
                slug=str(body.get('slug','')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para gerenciar vídeos.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                if access_status(current)['locked']: return self.end_json({'error':'Seu acesso está bloqueado. Ative um plano para continuar.'},402)
                action=str(body.get('action','create')).lower()
                videos=store_videos(slug)
                if action=='delete':
                    video_id=str(body.get('id','')).strip()[:40]
                    kept=[v for v in videos if str(v.get('id',''))!=video_id]
                    if len(kept)==len(videos): return self.end_json({'error':'Vídeo não encontrado.'},404)
                    save_store_videos(slug,kept); audit_event(slug,'video_deleted',{'video_id':video_id}); return self.end_json({'ok':True,'videos':kept})
                if len(videos)>=100: return self.end_json({'error':'Limite de 100 vídeos por loja atingido.'},400)
                item,error=clean_video_payload(body,slug)
                if error: return self.end_json({'error':error},400)
                videos.append(item); save_store_videos(slug,videos); audit_event(slug,'video_created',{'video_id':item['id'],'product_id':item['product_id']})
                return self.end_json({'ok':True,'video':item,'videos':videos})
            if route == '/api/orders':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para gerenciar pedidos.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                if access_status(current)['locked']: return self.end_json({'error':'Seu teste terminou. Ative um plano para continuar usando a gestão da loja.'},402)
                incoming=body.get('orders',[])
                if not isinstance(incoming,list) or len(incoming)>1000: return self.end_json({'error':'Pedidos inválidos'},400)
                existing=store_orders(slug); by_id={str(o.get('id')):o for o in existing if isinstance(o,dict) and o.get('id')}
                products=store_products(slug)
                for raw in incoming:
                    if not isinstance(raw,dict): continue
                    oid=str(raw.get('id','')).strip()[:80]
                    if not oid: continue
                    product=catalog_product(slug,raw)
                    if not product: continue
                    if oid in by_id:
                        # Client synchronization may not overwrite payment/customer facts.
                        if str(by_id[oid].get('status','')) in ('Novo','Pedido recebido') and raw.get('status') in ('Novo','Pedido recebido'):
                            by_id[oid]['status']=str(raw.get('status'))
                    else:
                        by_id[oid]={'id':oid,'product':product.get('name'),'product_id':product.get('id'),'price':product.get('price'),'customer_name':str(raw.get('customer_name','')).strip()[:100],'customer_phone':str(raw.get('customer_phone','')).strip()[:30],'session_id':str(raw.get('session_id','')).strip()[:100],'status':'Novo','created_at':time.time()}
                merged=list(by_id.values())[-1000:]; save_store_orders(slug,merged); audit_event(slug,'orders_synchronized',{'count':len(merged)})
                return self.end_json({'ok':True,'slug':slug,'orders':merged})
            if route == '/api/store':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para gerenciar produtos.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                if access_status(current)['locked']: return self.end_json({'error':'Seu teste terminou. Ative um plano para continuar gerenciando produtos.'},402)
                products=body.get('products',[])
                if not isinstance(products,list) or len(products)>500: return self.end_json({'error':'Catálogo inválido'},400)
                clean=[]
                for product in products:
                    item=clean_product(product)
                    if item: clean.append(item)
                if not clean: return self.end_json({'error':'Adicione pelo menos um produto válido'},400)
                save_store_products(slug,clean); audit_event(slug,'catalog_updated',{'count':len(clean)})
                return self.end_json({'ok':True,'slug':slug,'products':clean})
            if route == '/api/agent/settings':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para editar as regras da IA.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                raw=body.get('settings',{}); base=agent_settings(slug)
                try: min_margin=max(0,min(100,float(raw.get('min_margin',base['min_margin'])))); max_discount=max(0,min(100,float(raw.get('max_discount',base['max_discount']))))
                except Exception: return self.end_json({'error':'Informe percentuais válidos.'},400)
                settings={'min_margin':round(min_margin,2),'max_discount':round(max_discount,2),'require_approval':bool(raw.get('require_approval',base['require_approval'])),'tone':str(raw.get('tone',base['tone']))[:30],'persona_name':str(raw.get('persona_name',base['persona_name'])).strip()[:60] or base['persona_name'],'persona_description':str(raw.get('persona_description',base['persona_description'])).strip()[:180] or base['persona_description'],'pilot_mode':True,'negotiation_mode':'suggestion'}
                if settings['tone'] not in ('consultivo','direto','acolhedor'): settings['tone']='consultivo'
                save_agent_settings(slug,settings)
                return self.end_json({'ok':True,'slug':slug,'settings':settings})
            if route == '/api/seller-chat':
                message=str(body.get('message','')).strip()[:2000]; slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; session_id=str(body.get('session_id','')).strip()[:100]; catalog=store_products(slug)
                if not message: return self.end_json({'error':'Digite uma mensagem'},400)
                if not store_access_active(slug): return self.end_json({'ok':False,'reply':'Esta loja está temporariamente pausada enquanto o empreendedor ativa um plano.','ai_enabled':False},402)
                if not session_id: session_id=secrets.token_urlsafe(12)
                history=conversation_history(slug,session_id)
                reply=gemini_seller(message, history, catalog, agent_settings(slug))
                if reply:
                    is_concierge=bool(re.search(r'\b(presente|presentei|presentear|aniversário|aniversario|casamento|natal|dia das mães|dia das maes|dia dos pais|ocasião|ocasiao|lembrança|lembranca)\b', message.lower()))
                    if not is_concierge:
                        history += [{'role':'user','text':message},{'role':'assistant','text':reply}]; save_conversation(slug,session_id,history)
                    return self.end_json({'ok':True,'reply':reply,'ai_enabled':True,'session_id':session_id,'memory_enabled':not is_concierge,'mode':'concierge' if is_concierge else 'sales'})
                return self.end_json({'ok':False,'reply':'No momento não consegui consultar a vendedora. Tente novamente em alguns instantes.','ai_enabled':False},503)
            if route == '/api/generate':
                prompt=str(body.get('prompt','')).strip(); name=str(body.get('name','Meu aplicativo')).strip() or 'Meu aplicativo'
                if not prompt: return self.end_json({'error':'Descreva o aplicativo'},400)
                project=create_project(name,prompt)
                project['ai_enabled']=bool(os.environ.get('GEMINI_API_KEY'))
                return self.end_json({'ok':True,'project':project})
            command=str(body.get('command','')).strip(); files=body.get('files',{})
            if not command or not isinstance(files,dict): return self.end_json({'error':'Pedido ou arquivos ausentes'},400)
            updated=gemini_modify(command,files)
            if updated: return self.end_json({'ok':True,'files':updated,'ai_enabled':True})
            return self.end_json({'ok':False,'files':files,'ai_enabled':False,'message':'Configure GEMINI_API_KEY no servidor para ativar a IA.'})
        except Exception:
            return self.end_json({'error':'Não foi possível processar o projeto'},500)

    def do_DELETE(self):
        route=urlparse(self.path).path
        if route != '/api/videos': return self.end_json({'error':'Rota não encontrada'},404)
        try:
            if not self.same_origin(): return self.end_json({'error':'Origem não autorizada.'},403)
            current=session_user(self)
            if not current: return self.end_json({'error':'Faça login para gerenciar vídeos.'},401)
            query=parse_qs(urlparse(self.path).query); slug=query.get('loja',[''])[0].strip(); video_id=query.get('id',[''])[0].strip()
            if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
            videos=store_videos(slug); kept=[v for v in videos if str(v.get('id',''))!=video_id]
            if len(kept)==len(videos): return self.end_json({'error':'Vídeo não encontrado.'},404)
            save_store_videos(slug,kept); audit_event(slug,'video_deleted',{'video_id':video_id}); return self.end_json({'ok':True,'videos':kept})
        except Exception: return self.end_json({'error':'Não foi possível excluir o vídeo.'},500)


if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'ForgeAI disponível na porta {port}')
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
