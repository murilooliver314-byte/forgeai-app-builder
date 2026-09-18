from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, quote, parse_qs
import json, re, html, tarfile, io, time, os, urllib.request, urllib.error, hashlib, secrets

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / 'generated'
DATA.mkdir(exist_ok=True)
STORE_FILE = DATA / 'stores.json'
AUTH_FILE = DATA / 'auth.json'
ORDER_FILE = DATA / 'orders.json'
CHAT_FILE = DATA / 'conversations.json'
AGENT_FILE = DATA / 'agent_settings.json'
CUSTOMER_FILE = DATA / 'customers.json'
PUBLIC_BASE = 'https://forgeai-app-builder.onrender.com'
PAYMENT_FILE = DATA / 'payment_settings.json'
DEFAULT_AGENT_SETTINGS = {'min_margin':20,'max_discount':10,'require_approval':True,'tone':'consultivo'}
PLATFORM_COMMISSION_RATE = 0.05
PLAN_DAYS = {'BASICO':30,'PRO':180,'ENTERPRISE':365}
PLAN_PRICES = {'BASICO':98.90,'PRO':489.90,'ENTERPRISE':1089.90}
PLAN_LABELS = {'BASICO':'Básico','PRO':'Pro','ENTERPRISE':'Enterprise'}
DEFAULT_PRODUCTS = [{'name':'Produto especial','desc':'Qualidade e estilo para você.','price':'R$ 49,90'},{'name':'Mais vendido','desc':'O favorito dos clientes.','price':'R$ 79,90'},{'name':'Novidade','desc':'Acabou de chegar na loja.','price':'R$ 99,90'}]


def auth_data():
    try:
        data=json.loads(AUTH_FILE.read_text(encoding='utf-8')) if AUTH_FILE.exists() else {}
        if isinstance(data,dict): return {'users':data.get('users',{}),'sessions':data.get('sessions',{})}
    except Exception:
        pass
    return {'users':{},'sessions':{}}


def save_auth(data):
    AUTH_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def password_hash(password, salt=None):
    salt=salt or secrets.token_hex(16)
    digest=hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),salt.encode('utf-8'),120000).hex()
    return salt, digest


def session_user(handler):
    token=''
    for part in handler.headers.get('Cookie','').split(';'):
        if part.strip().startswith('vc_session='): token=part.strip().split('=',1)[1]
    data=auth_data(); email=data['sessions'].get(token)
    return data['users'].get(email) if email else None


def set_session(handler, token):
    handler.send_header('Set-Cookie',f'vc_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000')


def store_products(slug='vendacertaai'):
    try:
        data=json.loads(STORE_FILE.read_text(encoding='utf-8')) if STORE_FILE.exists() else {}
        products=data.get(slug)
        if isinstance(products,list) and products: return products
    except Exception:
        pass
    return DEFAULT_PRODUCTS.copy()


def save_store_products(slug, products):
    data={}
    try:
        data=json.loads(STORE_FILE.read_text(encoding='utf-8')) if STORE_FILE.exists() else {}
    except Exception:
        data={}
    data[slug]=products
    STORE_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def store_orders(slug='vendacertaai'):
    try:
        data=json.loads(ORDER_FILE.read_text(encoding='utf-8')) if ORDER_FILE.exists() else {}
        orders=data.get(slug,[])
        return orders if isinstance(orders,list) else []
    except Exception:
        return []


def save_store_orders(slug, orders):
    data={}
    try:
        data=json.loads(ORDER_FILE.read_text(encoding='utf-8')) if ORDER_FILE.exists() else {}
    except Exception:
        data={}
    data[slug]=orders
    ORDER_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def conversation_history(slug, session_id):
    try:
        data=json.loads(CHAT_FILE.read_text(encoding='utf-8')) if CHAT_FILE.exists() else {}
        history=data.get(slug,{}).get(session_id,[])
        return history if isinstance(history,list) else []
    except Exception:
        return []


def save_conversation(slug, session_id, history):
    data={}
    try:
        data=json.loads(CHAT_FILE.read_text(encoding='utf-8')) if CHAT_FILE.exists() else {}
    except Exception:
        data={}
    data.setdefault(slug,{})[session_id]=history[-20:]
    CHAT_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def agent_settings(slug='vendacertaai'):
    try:
        data=json.loads(AGENT_FILE.read_text(encoding='utf-8')) if AGENT_FILE.exists() else {}
        current=data.get(slug,{})
        if isinstance(current,dict):
            return {**DEFAULT_AGENT_SETTINGS,**current}
    except Exception:
        pass
    return DEFAULT_AGENT_SETTINGS.copy()


def save_agent_settings(slug, settings):
    data={}
    try:
        data=json.loads(AGENT_FILE.read_text(encoding='utf-8')) if AGENT_FILE.exists() else {}
    except Exception:
        data={}
    data[slug]=settings
    AGENT_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def customer_profile(slug, session_id):
    try:
        data=json.loads(CUSTOMER_FILE.read_text(encoding='utf-8')) if CUSTOMER_FILE.exists() else {}
        return data.get(slug,{}).get(session_id,{})
    except Exception:
        return {}


def save_customer_profile(slug, session_id, profile):
    data={}
    try:
        data=json.loads(CUSTOMER_FILE.read_text(encoding='utf-8')) if CUSTOMER_FILE.exists() else {}
    except Exception:
        data={}
    data.setdefault(slug,{})[session_id]=profile
    CUSTOMER_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def payment_token(slug):
    try:
        data=json.loads(PAYMENT_FILE.read_text(encoding='utf-8')) if PAYMENT_FILE.exists() else {}
        return str(data.get(slug,{}).get('access_token','')).strip()
    except Exception:
        return ''


def save_payment_token(slug, token):
    data={}
    try: data=json.loads(PAYMENT_FILE.read_text(encoding='utf-8')) if PAYMENT_FILE.exists() else {}
    except Exception: data={}
    data[slug]={'access_token':token,'updated_at':time.time()}
    PAYMENT_FILE.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')


def price_number(value):
    raw=re.sub(r'[^0-9,.-]','',str(value or '')).replace('.','').replace(',','.')
    try: return round(float(raw),2)
    except Exception: return 0.0


def create_mp_preference(title, price, order_id, slug):
    key=os.environ.get('MERCADO_PAGO_ACCESS_TOKEN','').strip() or payment_token(slug)
    if not key: return None, 'Conecte o Mercado Pago nas configurações da loja antes de pagar.'
    payload={'items':[{'title':str(title)[:120],'quantity':1,'unit_price':price,'currency_id':'BRL'}],'external_reference':f'{slug}:{order_id}','back_urls':{'success':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=success','pending':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=pending','failure':PUBLIC_BASE+'/vendacertaai?loja='+quote(slug)+'&payment=failure'},'auto_return':'approved','notification_url':PUBLIC_BASE+'/api/payment/webhook'}
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
    reference=str(payment.get('external_reference','')); status=str(payment.get('status',''))
    if reference.startswith('PLAN|'):
        parts=reference.split('|'); email=parts[1] if len(parts)>1 else ''; plan=parts[2] if len(parts)>2 else ''
        if status!='approved' or plan not in PLAN_DAYS: return False
        data=auth_data(); user=data['users'].get(email)
        if not user: return False
        now=time.time(); start=max(now,float(user.get('access_until',0) or 0)); user['access_plan']=plan; user['access_until']=start+PLAN_DAYS[plan]*86400; user['plan_status']='active'; user['last_payment_id']=str(payment.get('id','')); user['last_payment_at']=now; save_auth(data); return True
    if ':' not in reference: return False
    slug,order_id=reference.split(':',1); orders=store_orders(slug); changed=False
    for order in orders:
        if str(order.get('id'))==order_id:
            order['status']=payment_label(status); order['payment_id']=str(payment.get('id','')); order['payment_status']=status; order['updated_at']=time.time(); changed=True
    if changed: save_store_orders(slug,orders)
    return changed


def access_status(user):
    now=time.time(); trial_until=float(user.get('trial_ends_at',0) or 0); access_until=float(user.get('access_until',0) or 0); active_trial=trial_until>now; active_plan=access_until>now
    return {'trial_active':active_trial,'trial_ends_at':trial_until,'plan_active':active_plan,'plan':user.get('access_plan'),'access_until':access_until,'locked':not(active_trial or active_plan)}


def user_by_slug(slug):
    data=auth_data()
    return next((u for u in data['users'].values() if u.get('slug')==slug),None)


def store_access_active(slug):
    user=user_by_slug(slug)
    return True if not user else not access_status(user)['locked']


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
    catalog = catalog if isinstance(catalog,list) and catalog else DEFAULT_PRODUCTS
    catalog = '; '.join(f"{p.get('name','Produto')} — {p.get('price','Preço não informado')} — {p.get('desc','')}" for p in catalog if isinstance(p,dict))
    settings = {**DEFAULT_AGENT_SETTINGS, **(settings if isinstance(settings,dict) else {})}
    prompt = '''Você é a Consultora Inteligente da Equipe de Vendas com IA da VendaCertaAI. Responda em português brasileiro, de forma simpática, objetiva, personalizada e persuasiva, somente por texto. Use o catálogo e a memória da conversa para recomendar produtos e preços. Antecipe necessidades e sugira complementos somente quando fizer sentido. Nunca invente produtos, preços, descontos, estoque ou prazo de entrega. Nunca ofereça desconto por conta própria nesta versão; quando houver negociação, diga que pode verificar uma condição especial com a loja. Se o cliente quiser comprar, peça nome, produto e quantidade e diga que o pedido será encaminhado. Se não souber algo, diga que precisa confirmar com o vendedor.
REGRAS DA LOJA: margem mínima de ''' + str(settings['min_margin']) + '''%; desconto máximo de ''' + str(settings['max_discount']) + '''%; descontos exigem aprovação: ''' + ('sim' if settings['require_approval'] else 'não') + '''; tom: ''' + str(settings['tone']) + '''.
CATÁLOGO: ''' + catalog + '\nCONVERSA: ' + json.dumps(history[-8:], ensure_ascii=False) + '\nCLIENTE: ' + message
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
    html_doc='''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VendaCertaAI</title><link rel="stylesheet" href="styles.css"></head><body><header><div class="logo">✦ Venda<span>CertaAI</span></div><button class="outline back-btn" id="backBtn">← Voltar</button><nav><button data-view="home">Início</button><button data-view="store">Minha loja</button><button data-view="productsView">Produtos</button><button data-view="assistant">Vendedora IA</button><button data-view="orders">Pedidos</button></nav><span id="accountBadge" class="account-badge"></span><button class="theme-btn" id="themeBtn" aria-label="Alternar tema">◐</button><button class="outline" id="headerLogin">Entrar</button></header><main><section id="home" class="view active"><div class="hero"><small>SUA VENDEDORA PROFISSIONAL COM IA</small><h1>Venda mais.<br><em>Atenda melhor.</em></h1><p>Crie sua loja, mostre seus produtos e deixe a VendaCertaAI conversar com seus clientes.</p><div class="actions"><button class="primary" id="trialBtn">Iniciar teste grátis</button><button class="outline" id="loginBtn">Entrar</button><button class="outline" id="planAccessBtn" style="display:none">Ver planos e acesso</button></div><button class="adm-link" id="admBtn">ADM</button></div></section><section id="store" class="view"><div class="section-head"><div><small>LOJA PÚBLICA</small><h2>Loja da VendaCertaAI</h2><p>Produtos em destaque para seus clientes.</p></div><div class="actions"><button class="outline" id="shareStore">Compartilhar loja</button><button class="primary" id="openChat">Falar com a vendedora</button></div></div><div class="products" id="products"></div></section><section id="productsView" class="view"><div class="section-head"><div><small>CATÁLOGO</small><h2>Meus produtos</h2><p>Cadastre e organize seus produtos.</p></div><button class="primary" id="addProduct">＋ Adicionar produto</button></div><div class="products" id="productsAdmin"></div></section><section id="orders" class="view"><div class="section-head"><div><small>GESTÃO</small><h2>Pedidos</h2><p>Acompanhe seus pedidos e sua comissão.</p></div></div><div class="finance-summary" id="marketplaceSummary"><div><small>VENDAS PAGAS</small><strong>R$ 0,00</strong></div><div><small>SUA COMISSÃO ESTIMADA</small><strong>R$ 0,00</strong></div><div><small>REPASSE ESTIMADO</small><strong>R$ 0,00</strong></div></div><div class="orders" id="ordersList"><div class="empty">Nenhum pedido ainda. Os pedidos dos clientes aparecerão aqui.</div></div></section><section id="assistant" class="view"><div class="assistant-box"><div class="ai-identity"><div class="ai-avatar">✦</div><div><small>EQUIPE DE VENDAS COM IA</small><h2>Consultora inteligente</h2><p>Personalizada para esta loja e seus clientes.</p></div><span class="status">● Online</span></div><div class="chat" id="chat"><div class="bubble ai">Olá! Sou a vendedora da sua loja. Como posso ajudar?</div></div><form id="chatForm"><input id="chatInput" placeholder="Digite sua dúvida..." autocomplete="off"><button class="primary">Enviar</button></form><div class="agent-controls" id="agentControls"><div><small>REGRAS DA CONSULTORA</small><h3>Limites de autonomia</h3><p class="muted">Defina o que a IA pode considerar antes de oferecer uma condição comercial.</p></div><div class="agent-fields"><label>Margem mínima (%)<input id="minMargin" type="number" min="0" max="100" step="0.5"></label><label>Desconto máximo (%)<input id="maxDiscount" type="number" min="0" max="100" step="0.5"></label><label>Tom de atendimento<select id="agentTone"><option value="consultivo">Consultivo</option><option value="direto">Direto</option><option value="acolhedor">Acolhedor</option></select></label></div><label class="check-row"><input id="approvalRequired" type="checkbox"> Exigir minha aprovação para descontos</label><button class="outline" id="saveAgentSettings" type="button">Salvar regras da IA</button><span class="muted" id="agentSettingsStatus"></span></div></div></section><section id="access" class="view"><div class="access-card"><small>MEU ACESSO</small><h2 id="accessTitle">Teste grátis ativo</h2><p id="accessMessage">Você tem 48 horas para testar a VendaCertaAI.</p><div class="timer" id="accessTimer">48:00:00</div><div id="planOptions" style="display:none"><p class="muted">Escolha como deseja continuar usando a plataforma:</p><div class="plan-list"><button class="outline" data-plan="BASICO">Básico · R$ 98,90 · 30 dias</button><button class="outline" data-plan="PRO">Pro · R$ 489,90 · 180 dias</button><button class="outline" data-plan="ENTERPRISE">Enterprise · R$ 1.089,90 · 365 dias</button></div></div><button class="primary" id="codeBtn">Inserir código de acesso</button><p class="muted">Fale com Murilo pelo Instagram para adquirir acesso.</p><a href="https://www.instagram.com/geracao_ricabr?stkn=MXI0ZDlndDg1Yjk1aQ==" target="_blank">Abrir Instagram →</a></div></section><section id="admin" class="view"><div class="section-head"><div><small>CENTRAL ADM</small><h2>Painel do proprietário</h2><p>Gerencie empreendedores e códigos de acesso.</p></div><button class="primary" id="newCode">＋ Gerar código</button></div><div class="admin-grid"><div><b>Empreendedores</b><strong>1</strong><small>Conta cadastrada</small></div><div><b>Códigos disponíveis</b><strong id="codeCount">3</strong><small>Básico, Pro e Enterprise</small></div><div><b>Testes ativos</b><strong>1</strong><small>Em andamento</small></div></div><div class="code-list" id="codeList"><div><span>BÁSICO · 30 dias</span><b>DISPONÍVEL</b></div><div><span>PRO · 180 dias</span><b>DISPONÍVEL</b></div><div><span>ENTERPRISE · 365 dias</span><b>DISPONÍVEL</b></div></div></section></main><div id="modal" class="modal"><div class="modal-card"><button id="closeModal" class="close">×</button><div id="modalBody"></div></div></div><div id="toast" class="toast"></div><script src="app.js"></script></body></html>'''
    css='''*{box-sizing:border-box}body{margin:0;background:#09090f;color:#f8f7ff;font-family:Inter,Arial,sans-serif}button,input{font:inherit}button{cursor:pointer;border:0;color:inherit}.logo{font-size:20px;font-weight:800}.logo span{color:#a78bfa}header{height:70px;border-bottom:1px solid #2a2638;display:flex;align-items:center;padding:0 7%;gap:28px;background:#0d0d15}header nav{display:flex;gap:18px;margin:auto}header nav button,.adm-link{background:transparent;color:#aaa7ba;font-size:12px}.outline{background:transparent;border:1px solid #66508b;padding:10px 15px;border-radius:7px;color:#d8b4fe}.back-btn{padding:8px 12px;font-size:12px}.account-badge{color:#c084fc;font-size:11px;max-width:150px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.primary{background:linear-gradient(105deg,#7c3aed,#db2777);border-radius:7px;padding:11px 15px;font-weight:bold}.view{display:none;max-width:1200px;margin:auto;padding:55px 7%}.view.active{display:block}.hero{min-height:570px;display:flex;flex-direction:column;justify-content:center;background:radial-gradient(circle at 75% 35%,#68359a55,transparent 35%)}small{color:#c084fc;letter-spacing:1.5px;font-size:10px;font-weight:bold}.hero h1{font-size:clamp(42px,7vw,76px);line-height:.96;margin:17px 0;letter-spacing:-3px}.hero em{font-style:normal;background:linear-gradient(90deg,#c084fc,#f472b6);background-clip:text;color:transparent}.hero p{color:#aaa7b9;line-height:1.6;max-width:440px}.actions{display:flex;gap:10px;margin-top:22px}.adm-link{margin-top:40px;text-decoration:underline}.section-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:25px}.section-head h2{font-size:29px;margin:8px 0}.section-head p,.muted{color:#908da2;font-size:12px;margin:0}.products{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.product{background:#11111a;border:1px solid #2a2638;border-radius:10px;overflow:hidden}.product-art{height:150px;background:linear-gradient(135deg,#39205d,#a052ad)}.product:nth-child(2) .product-art{background:linear-gradient(135deg,#133b58,#5bc3da)}.product:nth-child(3) .product-art{background:linear-gradient(135deg,#58203c,#ed6ca9)}.product-info{padding:14px}.product-info strong{display:block}.product-info p{color:#aaa7b9;font-size:11px}.product-info .price{color:#c084fc;font-weight:bold;margin:10px 0}.orders .empty{border:1px dashed #463859;color:#9995a8;padding:35px;text-align:center;border-radius:9px}.assistant-box,.access-card{max-width:650px;margin:auto;background:#11111a;border:1px solid #302541;border-radius:12px;padding:25px}.status{color:#4ade80;font-size:11px}.chat{height:320px;overflow:auto;background:#0c0c13;border-radius:8px;padding:15px;margin:15px 0}.bubble{max-width:80%;padding:10px;border-radius:8px;margin:8px 0;font-size:12px}.bubble.ai{background:#281b3c}.bubble.user{background:#2a2937;margin-left:auto}.chat form{display:flex;gap:8px}.chat input{flex:1;background:#0b0b12;border:1px solid #39334a;color:#fff;padding:11px;border-radius:7px;outline:0}.timer{font-size:38px;color:#c084fc;margin:22px 0}.access-card a{color:#f472b6;font-size:12px}.plan-list{display:grid;gap:8px;margin:14px 0}.plan-list button{text-align:left}.admin-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.admin-grid>div{background:#11111a;border:1px solid #2a2638;border-radius:9px;padding:18px}.admin-grid b,.admin-grid strong,.admin-grid small{display:block}.admin-grid strong{font-size:27px;margin:12px 0}.admin-grid small{color:#908da2;font-size:11px}.code-list{margin-top:18px}.code-list>div{display:flex;justify-content:space-between;padding:14px;border-bottom:1px solid #292438;background:#11111a}.code-list b{color:#4ade80;font-size:10px}.modal{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;padding:20px;z-index:5}.modal.open{display:flex}.modal-card{background:#171522;border:1px solid #5a3b78;border-radius:12px;padding:25px;width:min(500px,100%);position:relative}.close{position:absolute;right:14px;top:9px;background:transparent;color:#aaa7ba;font-size:25px}.modal-card h2{margin:5px 0 14px}.modal-card input{display:block;width:100%;background:#0c0c13;border:1px solid #39334a;color:#fff;padding:11px;border-radius:7px;margin:9px 0 13px}.finance-summary{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:16px 0 22px}.finance-summary>div{padding:14px;border:1px solid #3c3150;border-radius:9px;background:#11111a}.finance-summary strong,.finance-summary small{display:block}.finance-summary strong{font-size:20px;margin-top:8px;color:#c084fc}.agent-controls{display:none;margin-top:18px;padding:18px;border:1px solid #3c3150;border-radius:10px;background:#0d0d15}.agent-controls.visible{display:block}.agent-controls h3{margin:7px 0 4px}.agent-fields{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:14px 0}.agent-fields label{color:#aaa7b9;font-size:11px}.agent-fields input,.agent-fields select{display:block;width:100%;margin-top:6px;background:#0b0b12;border:1px solid #39334a;color:#fff;padding:9px;border-radius:7px}.check-row{display:flex;gap:8px;align-items:center;color:#c9c4d4;font-size:11px;margin:12px 0}.agent-controls button{margin-top:8px}.agent-controls #agentSettingsStatus{margin-left:10px}.toast{position:fixed;bottom:22px;right:22px;background:#292139;border:1px solid #7653a8;border-radius:7px;padding:12px;transform:translateY(100px);opacity:0;transition:.3s}.toast.show{transform:translateY(0);opacity:1}@media(max-width:700px){header{padding:0 16px;gap:12px}header nav{display:none}.view{padding:35px 18px}.hero{min-height:500px}.products{grid-template-columns:1fr}.section-head{display:block}.section-head button{margin-top:15px}.admin-grid{grid-template-columns:1fr}.agent-fields{grid-template-columns:1fr}.finance-summary{grid-template-columns:1fr}.hero h1{font-size:49px}}
:root{--navy:#0f172a;--navy-soft:#172554;--purple:#6d5df5;--mint:#5eead4;--cyan:#22d3ee;--ice:#f5f7fa;--ink:#111827;--muted:#64748b;--line:#e2e8f0;--glass:rgba(255,255,255,.76)}
body{background:var(--ice);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;letter-spacing:-.01em}header{height:74px;padding:0 6%;gap:16px;background:rgba(255,255,255,.82);border-bottom:1px solid rgba(148,163,184,.22);box-shadow:0 8px 30px rgba(15,23,42,.05);backdrop-filter:blur(18px);position:sticky;top:0;z-index:4}.logo{color:var(--navy);font-size:19px;letter-spacing:-.04em}.logo span{background:linear-gradient(100deg,var(--purple),var(--cyan));background-clip:text;color:transparent}header nav{gap:8px}header nav button,.adm-link{color:#64748b;border-radius:10px;padding:9px 12px;transition:.2s}header nav button:hover{color:var(--navy);background:#eef2ff}.outline{border:1px solid #cbd5e1;background:rgba(255,255,255,.6);color:var(--navy);border-radius:10px;padding:10px 15px;transition:.2s}.outline:hover{border-color:var(--purple);box-shadow:0 8px 18px rgba(109,93,245,.14)}.primary{background:linear-gradient(110deg,var(--purple),#8b5cf6);border-radius:11px;padding:11px 16px;box-shadow:0 10px 24px rgba(109,93,245,.2);transition:transform .2s,box-shadow .2s}.primary:hover{transform:translateY(-2px);box-shadow:0 14px 28px rgba(109,93,245,.3)}.theme-btn{border:1px solid #cbd5e1;background:#fff;color:var(--navy);width:36px;height:36px;border-radius:10px;font-size:17px}.account-badge{color:var(--purple);font-weight:700}.view{max-width:1240px;padding:64px 6%}.hero{min-height:590px;border-radius:0 0 32px 32px;padding:72px 7%;background:radial-gradient(circle at 80% 25%,rgba(109,93,245,.18),transparent 28%),radial-gradient(circle at 68% 70%,rgba(34,211,238,.12),transparent 24%)}.hero h1{font-size:clamp(46px,7vw,82px);color:var(--navy);letter-spacing:-.07em}.hero em{background:linear-gradient(100deg,var(--purple),#0891b2);background-clip:text}.hero p,.section-head p,.muted{color:var(--muted)}small{color:#6256d9;letter-spacing:1.4px}.section-head{align-items:center}.section-head h2{color:var(--navy);letter-spacing:-.04em}.products{gap:20px}.product{background:var(--glass);border:1px solid rgba(148,163,184,.3);border-radius:20px;box-shadow:0 14px 35px rgba(15,23,42,.07);backdrop-filter:blur(14px);transition:transform .25s,box-shadow .25s}.product:hover{transform:translateY(-5px);box-shadow:0 20px 42px rgba(15,23,42,.13)}.product-art{height:170px;background:linear-gradient(135deg,#c7d2fe,#ddd6fe 48%,#bae6fd)}.product:nth-child(2) .product-art{background:linear-gradient(135deg,#cffafe,#99f6e4)}.product:nth-child(3) .product-art{background:linear-gradient(135deg,#e0e7ff,#f5d0fe)}.product-info{padding:18px}.product-info strong{color:var(--navy);font-size:15px}.product-info p{color:var(--muted);line-height:1.5}.product-info .price{color:var(--purple);font-size:17px}.product-info .actions{margin-top:14px}.product-info .actions .outline{padding:8px 11px;font-size:12px}.assistant-box,.access-card{background:rgba(255,255,255,.76);border:1px solid rgba(148,163,184,.3);box-shadow:0 20px 50px rgba(15,23,42,.08);backdrop-filter:blur(18px);border-radius:24px}.ai-identity{display:flex;align-items:center;gap:14px}.ai-identity h2{margin:4px 0;color:var(--navy);letter-spacing:-.04em}.ai-identity p{color:var(--muted);font-size:12px;margin:0}.ai-avatar{width:52px;height:52px;border-radius:17px;display:grid;place-items:center;color:#fff;font-size:25px;background:linear-gradient(135deg,var(--purple),var(--cyan));box-shadow:0 10px 24px rgba(109,93,245,.28)}.chat{background:rgba(241,245,249,.8);border:1px solid var(--line)}.bubble.ai{background:#e0e7ff;color:#1e1b4b;border-radius:14px}.bubble.user{background:var(--navy);border-radius:14px}.status{margin-left:auto;color:#059669}.chat input{background:#fff;border:1px solid var(--line);color:var(--ink)}.admin-grid>div,.code-list>div{background:rgba(255,255,255,.78);border-color:var(--line);border-radius:14px}.admin-grid strong{color:var(--navy)}.modal-card{background:rgba(255,255,255,.94);color:var(--ink);border:1px solid #cbd5e1;border-radius:20px;box-shadow:0 24px 80px rgba(15,23,42,.2)}.modal-card input{background:#f8fafc;border-color:#cbd5e1;color:var(--ink);border-radius:10px}.close{color:var(--muted)}.toast{background:var(--navy);border-color:var(--purple);border-radius:12px}.dark{background:#111827;color:#e5e7eb}.dark header{background:rgba(17,24,39,.84);border-color:#293548}.dark .logo,.dark .section-head h2,.dark .hero h1,.dark .product-info strong,.dark .ai-identity h2{color:#f8fafc}.dark .view{color:#e5e7eb}.dark .product,.dark .assistant-box,.dark .access-card,.dark .admin-grid>div,.dark .code-list>div,.dark .modal-card{background:rgba(31,41,55,.82);border-color:#374151}.dark .product-info p,.dark .section-head p,.dark .muted,.dark .ai-identity p{color:#94a3b8}.dark .chat{background:#111827;border-color:#374151}.dark .chat input{background:#1f2937;border-color:#475569;color:#fff}.dark .bubble.ai{background:#312e81;color:#e0e7ff}.dark .theme-btn,.dark .outline{background:#1f2937;border-color:#475569;color:#e2e8f0}@media(max-width:700px){header{height:64px;padding:0 14px;gap:8px}.account-badge{display:none}.theme-btn{width:32px;height:32px}.back-btn{padding:7px 9px;font-size:11px}.hero{border-radius:0;padding:48px 22px}.view{padding:38px 18px}.ai-identity{align-items:flex-start}.status{font-size:10px}}
'''
    js=r'''let products=[{name:'Produto especial',desc:'Qualidade e estilo para você.',price:'R$ 49,90'},{name:'Mais vendido',desc:'O favorito dos clientes.',price:'R$ 79,90'},{name:'Novidade',desc:'Acabou de chegar na loja.',price:'R$ 99,90'}];let orders=[];let authUser=null;let storeSlug=new URLSearchParams(window.location.search).get('loja')||'vendacertaai';let customerSession=localStorage.getItem('vc_customer_session_'+storeSlug)||('c_'+Date.now()+'_'+Math.random().toString(36).slice(2));localStorage.setItem('vc_customer_session_'+storeSlug,customerSession);let currentView='home';let screenHistory=['home'];const $=id=>document.getElementById(id);function view(id,record=true){if((id==='productsView'||id==='orders')&&!authUser){openLogin();toast('Entre para acessar sua área de gestão.');return}if(!$(id)||id===currentView)return;document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));$(id).classList.add('active');if(record)screenHistory.push(id);currentView=id;window.scrollTo(0,0)}function toast(t){$('toast').textContent=t;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),2500)}function modal(html){$('modalBody').innerHTML=html;$('modal').classList.add('open')}function dismissModal(){ $('modal').classList.remove('open') }function renderProducts(){const html=products.map((p,i)=>`<article class="product"><div class="product-art"></div><div class="product-info"><strong>${p.name}</strong><p>${p.desc}</p><div class="price">${p.price}</div><button class="primary" data-buy="${i}">Adicionar ao pedido</button></div></article>`).join('');$('products').innerHTML=html;$('productsAdmin').innerHTML=products.map((p,i)=>`<article class="product"><div class="product-art"></div><div class="product-info"><strong>${p.name}</strong><p>${p.desc}</p><div class="price">${p.price}</div><div class="actions"><button class="outline" data-edit="${i}">Editar</button><button class="outline" data-delete="${i}">Excluir</button></div></div></article>`).join('');document.querySelectorAll('[data-buy]').forEach(b=>b.onclick=async()=>{const chosen=products[Number(b.dataset.buy)],order={id:String(Date.now()).slice(-6),product:chosen.name,price:chosen.price,status:'Novo'};if(authUser){orders.push(order);syncOrders();view('orders');renderOrders();toast('Pedido criado com sucesso')}else{const saved=JSON.parse(localStorage.getItem('vc_customer_profile_'+storeSlug)||'{}');modal('<small>FINALIZAR PEDIDO</small><h2>Como podemos te identificar?</h2><p class="muted">Precisamos destes dados para a loja acompanhar seu pedido.</p><input id="customerName" placeholder="Seu nome"><input id="customerPhone" placeholder="Seu telefone"><button class="primary" id="customerSubmit">Enviar pedido</button>');setTimeout(()=>{$('customerName').value=saved.name||'';$('customerPhone').value=saved.phone||'';$('customerSubmit').onclick=async()=>{const name=$('customerName').value.trim(),phone=$('customerPhone').value.trim();if(!name||!phone){toast('Informe nome e telefone');return}try{const r=await fetch('/api/payment/create',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,session_id:customerSession,customer:{name,phone},order})});const d=await r.json();if(!r.ok)throw new Error(d.error||'Não foi possível criar o pagamento.');localStorage.setItem('vc_customer_profile_'+storeSlug,JSON.stringify({name,phone}));dismissModal();window.location.href=d.checkout_url}catch(e){toast('Não foi possível enviar o pedido agora.')}}},0)}});document.querySelectorAll('[data-edit]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.edit),p=products[i];modal('<small>EDITAR PRODUTO</small><h2>Atualizar produto</h2><input id="editName" placeholder="Nome do produto"><input id="editDesc" placeholder="Descrição"><input id="editPrice" placeholder="Preço"><button class="primary" id="updateProduct">Salvar alterações</button>');setTimeout(()=>{$('editName').value=p.name;$('editDesc').value=p.desc;$('editPrice').value=p.price;$('updateProduct').onclick=()=>{const name=$('editName').value.trim(),desc=$('editDesc').value.trim(),price=$('editPrice').value.trim();if(!name||!price){toast('Informe nome e preço');return}products[i]={name,desc:desc||'Produto disponível na loja.',price};syncProducts();renderProducts();dismissModal();toast('Produto atualizado!')}},0)});document.querySelectorAll('[data-delete]').forEach(b=>b.onclick=()=>{const i=Number(b.dataset.delete);modal('<small>EXCLUIR PRODUTO</small><h2>Tem certeza?</h2><p class="muted">Este produto será removido da loja.</p><button class="primary" id="confirmDelete">Excluir produto</button>');setTimeout(()=>{$('confirmDelete').onclick=()=>{products.splice(i,1);if(!products.length)products.push({name:'Novo produto',desc:'Adicione uma descrição.',price:'R$ 0,00'});syncProducts();renderProducts();dismissModal();toast('Produto excluído!')}},0)});};function syncProducts(){localStorage.setItem('vendacerta_products',JSON.stringify(products));fetch('/api/store',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,products})}).catch(()=>{})}function renderOrders(){if(!orders.length){$('ordersList').innerHTML='<div class="empty">Nenhum pedido ainda. Os pedidos dos clientes aparecerão aqui.</div>';return}$('ordersList').innerHTML=orders.map(o=>'<div class="product"><div class="product-info"><strong>Pedido '+o.id+'</strong><p>'+safeText(o.product)+' · 1 unidade</p><div class="price">'+safeText(o.price)+'</div><span class="muted">Cliente: '+safeText(o.customer_name||'Não informado')+(o.customer_phone?' · '+safeText(o.customer_phone):'')+'<br>Status: '+safeText(o.status||'Novo')+'</span></div></div>').join('')}function syncOrders(){localStorage.setItem('vendacerta_orders',JSON.stringify(orders));fetch('/api/orders',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({slug:storeSlug,orders})}).catch(()=>{})}async function loadProducts(){try{const r=await fetch('/api/store?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(d.products?.length){products=d.products;renderProducts()}}catch(e){}}async function loadOrders(){if(!authUser){orders=[];renderOrders();return}try{const r=await fetch('/api/orders?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(Array.isArray(d.orders)){orders=d.orders;renderOrders()}}catch(e){}}async function loadMarketplaceSummary(){if(!authUser)return;try{const r=await fetch('/api/marketplace/summary?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(d.ok){const s=d.summary;$('marketplaceSummary').innerHTML='<div><small>VENDAS PAGAS</small><strong>R$ '+Number(s.gross_sales||0).toFixed(2).replace('.',',')+'</strong></div><div><small>SUA COMISSÃO ESTIMADA</small><strong>R$ '+Number(s.platform_commission||0).toFixed(2).replace('.',',')+'</strong></div><div><small>REPASSE ESTIMADO</small><strong>R$ '+Number(s.seller_net_estimate||0).toFixed(2).replace('.',',')+'</strong></div>'}}catch(e){}}async function loadAccessStatus(){if(!authUser)return;try{const r=await fetch('/api/access/status');const d=await r.json();if(!d.ok)return;const a=d.access;if(a.locked){$('planAccessBtn').style.display='inline-block';$('accessTitle').textContent='Seu teste terminou';if(currentView!=='access')view('access',false);$('accessMessage').textContent='Escolha um plano para continuar usando a VendaCertaAI.';$('accessTimer').style.display='none';$('planOptions').style.display='block'}else{$('planAccessBtn').style.display='none';$('accessTitle').textContent=a.plan_active?'Acesso '+(a.plan||'ativo'):'Teste grátis ativo';$('accessMessage').textContent=a.plan_active?'Seu acesso está ativo.':'Você tem 48 horas para testar a VendaCertaAI.'}}catch(e){}}async function checkSession(){try{const r=await fetch('/api/auth/me');const d=await r.json();if(d.ok&&d.user){storeSlug=d.user.slug;setAuth(d.user);window.history.replaceState({},'',window.location.pathname+'?loja='+encodeURIComponent(storeSlug));loadProducts();loadOrders();loadMarketplaceSummary();loadAgentSettings()}}catch(e){}}document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));async function authPost(route,payload){const r=await fetch(route,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});const d=await r.json();if(!r.ok)throw new Error(d.error||'Não foi possível concluir.');return d}function setAuth(user){authUser=user;$('accountBadge').textContent=user?'Olá, '+user.name:'';$('headerLogin').textContent=user?'Sair':'Entrar';$('headerLogin').onclick=user?logout:openLogin;$('agentControls').classList.toggle('visible',!!user);if(user){loadAgentSettings();loadAccessStatus()}}async function loadAgentSettings(){if(!authUser)return;try{const r=await fetch('/api/agent/settings?loja='+encodeURIComponent(storeSlug));const d=await r.json();if(d.ok){$('minMargin').value=d.settings.min_margin;$('maxDiscount').value=d.settings.max_discount;$('approvalRequired').checked=!!d.settings.require_approval;$('agentTone').value=d.settings.tone}}catch(e){}}async function saveAgentSettings(){try{const payload={slug:storeSlug,settings:{min_margin:Number($('minMargin').value),max_discount:Number($('maxDiscount').value),require_approval:$('approvalRequired').checked,tone:$('agentTone').value}};const d=await authPost('/api/agent/settings',payload);$('agentSettingsStatus').textContent='Regras salvas';setTimeout(()=>$('agentSettingsStatus').textContent='',2500);toast('Regras da IA atualizadas!')}catch(e){toast(e.message)}}function enterStore(user,message){storeSlug=user.slug;setAuth(user);window.history.replaceState({},'',window.location.pathname+'?loja='+encodeURIComponent(storeSlug));dismissModal();view('store');loadProducts();loadOrders();loadMarketplaceSummary();loadAgentSettings();toast(message)}async function logout(){await fetch('/api/auth/logout',{method:'POST'});setAuth(null);view('home');toast('Você saiu da conta.')}function openSignup(){modal('<small>TESTE GRÁTIS</small><h2>Comece em 48 horas</h2><input id="signupName" placeholder="Seu nome"><input id="signupBusiness" placeholder="Nome do negócio"><input id="signupEmail" placeholder="Seu e-mail" type="email"><input id="signupPassword" placeholder="Crie uma senha (mín. 6 caracteres)" type="password"><button class="primary" id="signupSubmit">Criar minha conta</button>');setTimeout(()=>{$('signupSubmit').onclick=async()=>{try{const d=await authPost('/api/auth/signup',{name:$('signupName').value,business:$('signupBusiness').value,email:$('signupEmail').value,password:$('signupPassword').value});enterStore(d.user,'Teste grátis iniciado!')}catch(e){toast(e.message)} }},0)}function openLogin(){modal('<small>ENTRAR</small><h2>Acesse sua conta</h2><input id="loginEmail" placeholder="E-mail" type="email"><input id="loginPassword" placeholder="Senha" type="password"><button class="primary" id="loginSubmit">Entrar</button>');setTimeout(()=>{$('loginSubmit').onclick=async()=>{try{const d=await authPost('/api/auth/login',{email:$('loginEmail').value,password:$('loginPassword').value});enterStore(d.user,'Login realizado!')}catch(e){toast(e.message)} }},0)}$('trialBtn').onclick=openSignup;$('loginBtn').onclick=$('headerLogin').onclick=openLogin;$('planAccessBtn').onclick=()=>view('access');document.querySelectorAll('[data-plan]').forEach(b=>b.onclick=async()=>{try{const d=await authPost('/api/access/checkout',{plan:b.dataset.plan});if(d.checkout_url)window.location.href=d.checkout_url;else toast('Checkout indisponível.')}catch(e){toast(e.message)}});$('admBtn').onclick=()=>{modal('<small>ÁREA RESTRITA</small><h2>Senha do ADM</h2><input id="admPassword" type="password" placeholder="Digite a senha"><button class="primary" id="admLogin">Acessar ADM</button>');setTimeout(()=>{$('admLogin').onclick=()=>{if($('admPassword').value==='muriloadm321'){dismissModal();view('admin')}else toast('Senha incorreta')}},0)};function storeLink(){return window.location.origin+window.location.pathname+'?loja='+encodeURIComponent(storeSlug)}$('shareStore').onclick=()=>{const link=storeLink();modal('<small>LINK DA LOJA PÚBLICA</small><h2>Compartilhe sua loja</h2><p class="muted">Envie este link para seus clientes verem os produtos:</p><input id="storeLink" value="'+link+'" readonly><button class="primary" id="copyStore">Copiar link</button><button class="outline" onclick="window.open(\''+link+'\',\'_blank\')">Abrir loja pública</button>');setTimeout(()=>{$('copyStore').onclick=()=>{navigator.clipboard?.writeText(link);toast('Link copiado!');dismissModal()}},0)};$('openChat').onclick=()=>view('assistant');$('codeBtn').onclick=()=>modal('<small>CÓDIGO DE ACESSO</small><h2>Digite seu código</h2><input placeholder="Ex.: PRO-2026-XXXX"><button class="primary" onclick="dismissModal();toast(\'Código validado! Acesso liberado.\')">Liberar acesso</button>');$('newCode').onclick=()=>{modal('<small>NOVO CÓDIGO</small><h2>Escolha o plano</h2><select id="planSelect"><option value="BASICO">Básico · 30 dias</option><option value="PRO">Pro · 180 dias</option><option value="ENTERPRISE">Enterprise · 365 dias</option></select><button class="primary" id="createCode">Gerar código</button>');setTimeout(()=>{$('createCode').onclick=()=>{const p=$('planSelect').value;const labels={BASICO:'BÁSICO',PRO:'PRO',ENTERPRISE:'ENTERPRISE'};const days={BASICO:30,PRO:180,ENTERPRISE:365};const code=p+'-'+Math.random().toString(36).slice(2,8).toUpperCase();modal('<small>'+labels[p]+' · '+days[p]+' dias</small><h2>Código criado</h2><p class="muted">Entregue este código ao empreendedor:</p><div class="timer">'+code+'</div><button class="primary" onclick="dismissModal()">Fechar</button>')}} ,0)};$('addProduct').onclick=()=>{modal('<small>NOVO PRODUTO</small><h2>Adicionar produto</h2><input id="productName" placeholder="Nome do produto"><input id="productDesc" placeholder="Descrição"><input id="productPrice" placeholder="Preço"><button class="primary" id="saveProduct">Salvar produto</button>');setTimeout(()=>{$('saveProduct').onclick=()=>{const name=$('productName').value.trim(),desc=$('productDesc').value.trim(),price=$('productPrice').value.trim();if(!name||!price){toast('Informe nome e preço');return}products.push({name,desc:desc||'Produto disponível na loja.',price});syncProducts();renderProducts();dismissModal();toast('Produto salvo e catálogo atualizado!')}},0)};function safeText(t){const d=document.createElement('div');d.textContent=t;return d.innerHTML}$('chatForm').onsubmit=async e=>{e.preventDefault();const i=$('chatInput');if(!i.value.trim())return;const t=i.value.trim();const c=$('chat');c.innerHTML+='<div class="bubble user">'+safeText(t)+'</div>';i.value='';c.innerHTML+='<div class="bubble ai" id="typing">Estou consultando os produtos...</div>';c.scrollTop=c.scrollHeight;try{const r=await fetch('/api/seller-chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:t,catalog:products,slug:storeSlug,session_id:customerSession})});const d=await r.json();$('typing')?.remove();c.innerHTML+='<div class="bubble ai">'+safeText(d.reply||'Não consegui responder agora.')+'</div>'}catch(err){$('typing')?.remove();c.innerHTML+='<div class="bubble ai">Não consegui falar com a vendedora agora. Tente novamente.</div>'}c.scrollTop=c.scrollHeight};$('themeBtn').onclick=()=>{document.body.classList.toggle('dark');localStorage.setItem('vc_theme',document.body.classList.contains('dark')?'dark':'light')};$('saveAgentSettings').onclick=saveAgentSettings;if(localStorage.getItem('vc_theme')==='dark')document.body.classList.add('dark');$('backBtn').onclick=()=>{if(screenHistory.length>1){screenHistory.pop();view(screenHistory[screenHistory.length-1],false)}};$('closeModal').onclick=dismissModal;renderProducts();renderOrders();loadProducts();loadOrders();checkSession();const paymentQuery=new URLSearchParams(window.location.search),paymentResult=paymentQuery.get('payment'),returnedPayment=paymentQuery.get('payment_id');if(returnedPayment)fetch('/api/payment/status?id='+encodeURIComponent(returnedPayment)).then(r=>r.json()).then(d=>{if(d.ok)toast('Status do pagamento: '+d.label)}).catch(()=>{});if(paymentResult==='success')setTimeout(()=>toast('Pagamento aprovado! Pedido recebido pela loja.'),700);if(paymentResult==='pending')setTimeout(()=>toast('Pagamento em análise. A loja acompanhará o status.'),700);if(paymentResult==='failure')setTimeout(()=>toast('Pagamento não concluído. Você pode tentar novamente.'),700);if(new URLSearchParams(window.location.search).has('loja')){view('store')}'''
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
    page=page.replace('<link rel="stylesheet" href="styles.css">','<style>'+files['styles.css']+'</style>')
    page=page.replace('<script src="app.js"></script>','<script>'+files['app.js'].replace('</script>','<\\/script>')+'</script>')
    return page.encode('utf-8')


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def end_json(self, data, status=200, cookie=None, clear_cookie=False):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw)))
        if cookie: set_session(self,cookie)
        if clear_cookie: self.send_header('Set-Cookie','vc_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0')
        self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        if path in ('/vendacertaai','/vendacertaai/'):
            raw=vendacerta_page(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
        if path=='/api/health': return self.end_json({'ok':True,'service':'ForgeAI'})
        if path=='/api/auth/me':
            user=session_user(self)
            return self.end_json({'ok':bool(user),'user':({k:user.get(k) for k in ('id','name','business','slug','trial_ends_at')} if user else None)})
        if path=='/api/access/status':
            current=session_user(self)
            if not current: return self.end_json({'ok':False,'locked':True,'user':None})
            return self.end_json({'ok':True,'user':{k:current.get(k) for k in ('name','business','slug')},'access':access_status(current)})
        if path=='/api/store':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0]
            return self.end_json({'ok':True,'slug':slug,'products':store_products(slug)})
        if path=='/api/orders':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0]
            return self.end_json({'ok':True,'slug':slug,'orders':store_orders(slug)})
        if path=='/api/agent/settings':
            slug=parse_qs(urlparse(self.path).query).get('loja',['vendacertaai'])[0]
            current=session_user(self)
            if not current or current.get('slug') != slug: return self.end_json({'error':'Faça login para acessar as regras da IA.'},401)
            return self.end_json({'ok':True,'slug':slug,'settings':agent_settings(slug)})
        if path=='/api/payment/status':
            payment_id=parse_qs(urlparse(self.path).query).get('id',[''])[0]; payment=mp_payment(payment_id)
            if not payment: return self.end_json({'error':'Pagamento não encontrado.'},404)
            apply_payment_update(payment)
            return self.end_json({'ok':True,'payment_id':payment.get('id'),'status':payment.get('status'),'label':payment_label(payment.get('status'))})
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
        if route not in ('/api/generate','/api/modify','/api/seller-chat','/api/store','/api/orders','/api/order/create','/api/payment/create','/api/access/checkout','/api/payment/webhook','/api/marketplace/summary','/api/customer/profile','/api/agent/settings','/api/auth/signup','/api/auth/login','/api/auth/logout'): return self.end_json({'error':'Rota não encontrada'},404)
        try:
            length=int(self.headers.get('Content-Length','0')); body=json.loads(self.rfile.read(length) or '{}')
            if route == '/api/auth/signup':
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password','')); name=str(body.get('name','')).strip(); business=str(body.get('business','')).strip()
                if '@' not in email or len(password)<6 or not name or not business: return self.end_json({'error':'Preencha nome, negócio, e-mail e senha com pelo menos 6 caracteres.'},400)
                data=auth_data()
                if email in data['users']: return self.end_json({'error':'Este e-mail já está cadastrado.'},409)
                slug=slugify(business); used={u.get('slug') for u in data['users'].values()}; base=slug; n=2
                while slug in used: slug=f'{base}-{n}'; n+=1
                salt,digest=password_hash(password); now=time.time(); user={'id':secrets.token_hex(12),'email':email,'name':name,'business':business,'slug':slug,'salt':salt,'password_hash':digest,'trial_ends_at':now+48*3600,'access_plan':None,'access_until':0,'plan_status':'trial'}
                data['users'][email]=user; token=secrets.token_urlsafe(32); data['sessions'][token]=email; save_auth(data); save_store_products(slug,DEFAULT_PRODUCTS.copy()); save_store_orders(slug,[]); save_agent_settings(slug,DEFAULT_AGENT_SETTINGS.copy())
                return self.end_json({'ok':True,'user':{k:user[k] for k in ('id','name','business','slug','trial_ends_at')}},cookie=token)
            if route == '/api/auth/login':
                email=str(body.get('email','')).strip().lower(); password=str(body.get('password','')); data=auth_data(); user=data['users'].get(email)
                if not user: return self.end_json({'error':'E-mail ou senha inválidos.'},401)
                _,digest=password_hash(password,user.get('salt'))
                if not secrets.compare_digest(digest,user.get('password_hash','')): return self.end_json({'error':'E-mail ou senha inválidos.'},401)
                token=secrets.token_urlsafe(32); data['sessions'][token]=email; save_auth(data)
                return self.end_json({'ok':True,'user':{k:user[k] for k in ('id','name','business','slug','trial_ends_at')}},cookie=token)
            if route == '/api/auth/logout':
                token=''
                for part in self.headers.get('Cookie','').split(';'):
                    if part.strip().startswith('vc_session='): token=part.strip().split('=',1)[1]
                data=auth_data(); data['sessions'].pop(token,None); save_auth(data)
                return self.end_json({'ok':True},clear_cookie=True)
            if route == '/api/access/checkout':
                current=session_user(self)
                if not current: return self.end_json({'error':'Entre na sua conta para escolher um plano.'},401)
                plan=str(body.get('plan','')).upper().strip()
                if plan not in PLAN_PRICES: return self.end_json({'error':'Plano inválido.'},400)
                preference,error=create_plan_preference(current.get('email',''),plan)
                if error: return self.end_json({'error':error},503)
                return self.end_json({'ok':True,'plan':plan,'price':PLAN_PRICES[plan],'days':PLAN_DAYS[plan],'checkout_url':preference.get('init_point') or preference.get('sandbox_init_point'),'preference_id':preference.get('id')})
            if route == '/api/customer/profile':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; session_id=str(body.get('session_id','')).strip()[:100]; name=str(body.get('name','')).strip()[:100]; phone=str(body.get('phone','')).strip()[:30]
                if not session_id or not name or not phone: return self.end_json({'error':'Informe nome e telefone.'},400)
                previous=customer_profile(slug,session_id); profile={'name':name,'phone':phone,'updated_at':time.time(),'orders':previous.get('orders',[])}; save_customer_profile(slug,session_id,profile)
                return self.end_json({'ok':True,'profile':profile})
            if route == '/api/payment/webhook':
                notification_type=str(body.get('type') or body.get('topic') or '').lower(); payment_id=(body.get('data',{}).get('id') if isinstance(body.get('data',{}),dict) else body.get('id'))
                if notification_type in ('payment','merchant_order') and payment_id:
                    payment=mp_payment(payment_id)
                    if payment: apply_payment_update(payment)
                return self.end_json({'ok':True})
            if route == '/api/payment/create':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'
                if not store_access_active(slug): return self.end_json({'error':'O período de teste terminou. Ative um plano para continuar vendendo.'},402)
                order=body.get('order',{}); session_id=str(body.get('session_id','')).strip()[:100]; customer=body.get('customer',{}) if isinstance(body.get('customer',{}),dict) else {}
                if not isinstance(order,dict) or not str(order.get('product','')).strip(): return self.end_json({'error':'Pedido inválido.'},400)
                amount=price_number(order.get('price',''))
                if amount <= 0: return self.end_json({'error':'Preço inválido para pagamento.'},400)
                order_id=str(order.get('id') or str(time.time()).replace('.','')[-8:]); commission_amount=round(amount*PLATFORM_COMMISSION_RATE,2); seller_amount=round(amount-commission_amount,2); preference,error=create_mp_preference(order.get('product'),amount,order_id,slug)
                if error: return self.end_json({'error':error},503)
                if session_id and str(customer.get('name','')).strip() and str(customer.get('phone','')).strip():
                    previous=customer_profile(slug,session_id); profile={'name':str(customer.get('name')).strip()[:100],'phone':str(customer.get('phone')).strip()[:30],'updated_at':time.time(),'orders':previous.get('orders',[])+[str(order.get('product',''))]}; save_customer_profile(slug,session_id,profile)
                orders=store_orders(slug); saved={'id':order_id,'product':str(order.get('product')),'price':str(order.get('price','')),'customer_name':str(customer.get('name','')).strip()[:100],'customer_phone':str(customer.get('phone','')).strip()[:30],'session_id':session_id,'status':'Pagamento pendente','payment_preference_id':preference.get('id'),'checkout_url':preference.get('init_point') or preference.get('sandbox_init_point'),'commission_rate':PLATFORM_COMMISSION_RATE,'commission_amount':commission_amount,'seller_amount_estimate':seller_amount,'settlement_status':'Aguardando conexão OAuth do vendedor'}; orders.append(saved); save_store_orders(slug,orders)
                return self.end_json({'ok':True,'order':saved,'checkout_url':saved['checkout_url'],'payment_provider':'mercadopago'})
            if route == '/api/order/create':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'
                if not store_access_active(slug): return self.end_json({'error':'O período de teste terminou. A loja precisa ativar um plano para aceitar novos pedidos.'},402)
                order=body.get('order',{}); session_id=str(body.get('session_id','')).strip()[:100]; customer=body.get('customer',{}) if isinstance(body.get('customer',{}),dict) else {}
                if session_id and str(customer.get('name','')).strip() and str(customer.get('phone','')).strip():
                    previous=customer_profile(slug,session_id); profile={'name':str(customer.get('name')).strip()[:100],'phone':str(customer.get('phone')).strip()[:30],'updated_at':time.time(),'orders':previous.get('orders',[])}; profile['orders']=profile['orders']+ [str(order.get('product',''))]; save_customer_profile(slug,session_id,profile)
                
                if not isinstance(order,dict) or not str(order.get('product','')).strip(): return self.end_json({'error':'Pedido inválido.'},400)
                orders=store_orders(slug); orders.append({'id':str(order.get('id') or str(time.time()).replace('.','')[-8:]),'product':str(order.get('product')),'price':str(order.get('price','')),'customer_name':str(customer.get('name','')).strip()[:100],'customer_phone':str(customer.get('phone','')).strip()[:30],'session_id':session_id,'status':'Novo'})
                save_store_orders(slug,orders)
                return self.end_json({'ok':True,'order':orders[-1]})
            if route == '/api/orders':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para gerenciar pedidos.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                if access_status(current)['locked']: return self.end_json({'error':'Seu teste terminou. Ative um plano para continuar usando a gestão da loja.'},402)
                orders=body.get('orders',[])
                if not isinstance(orders,list): return self.end_json({'error':'Pedidos inválidos'},400)
                save_store_orders(slug,orders)
                return self.end_json({'ok':True,'slug':slug,'orders':orders})
            if route == '/api/store':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para gerenciar produtos.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                if access_status(current)['locked']: return self.end_json({'error':'Seu teste terminou. Ative um plano para continuar gerenciando produtos.'},402)
                products=body.get('products',[])
                if not isinstance(products,list) or not products: return self.end_json({'error':'Catálogo inválido'},400)
                clean=[p for p in products if isinstance(p,dict) and str(p.get('name','')).strip() and str(p.get('price','')).strip()]
                if not clean: return self.end_json({'error':'Adicione pelo menos um produto'},400)
                save_store_products(slug,clean)
                return self.end_json({'ok':True,'slug':slug,'products':clean})
            if route == '/api/agent/settings':
                slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; current=session_user(self)
                if not current: return self.end_json({'error':'Faça login para editar as regras da IA.'},401)
                if current.get('slug') != slug: return self.end_json({'error':'Acesso não autorizado para esta loja.'},403)
                raw=body.get('settings',{}); base=agent_settings(slug)
                try: min_margin=max(0,min(100,float(raw.get('min_margin',base['min_margin'])))); max_discount=max(0,min(100,float(raw.get('max_discount',base['max_discount']))))
                except Exception: return self.end_json({'error':'Informe percentuais válidos.'},400)
                settings={'min_margin':round(min_margin,2),'max_discount':round(max_discount,2),'require_approval':bool(raw.get('require_approval',base['require_approval'])),'tone':str(raw.get('tone',base['tone']))[:30]}
                if settings['tone'] not in ('consultivo','direto','acolhedor'): settings['tone']='consultivo'
                save_agent_settings(slug,settings)
                return self.end_json({'ok':True,'slug':slug,'settings':settings})
            if route == '/api/seller-chat':
                message=str(body.get('message','')).strip(); slug=str(body.get('slug','vendacertaai')).strip() or 'vendacertaai'; session_id=str(body.get('session_id','')).strip()[:100]; catalog=body.get('catalog',[])
                if not message: return self.end_json({'error':'Digite uma mensagem'},400)
                if not store_access_active(slug): return self.end_json({'ok':False,'reply':'Esta loja está temporariamente pausada enquanto o empreendedor ativa um plano.','ai_enabled':False},402)
                if not session_id: session_id=secrets.token_urlsafe(12)
                history=conversation_history(slug,session_id)
                reply=gemini_seller(message, history, catalog, agent_settings(slug))
                if reply:
                    history += [{'role':'user','text':message},{'role':'assistant','text':reply}]; save_conversation(slug,session_id,history)
                    return self.end_json({'ok':True,'reply':reply,'ai_enabled':True,'session_id':session_id,'memory_enabled':True})
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

if __name__=='__main__':
    port=int(os.environ.get('PORT','8000'))
    print(f'ForgeAI disponível na porta {port}')
    ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
