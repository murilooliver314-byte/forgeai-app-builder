from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, quote
import json, re, html, tarfile, io, time, os, urllib.request, urllib.error

ROOT = Path(__file__).parent.resolve()
DATA = ROOT / 'generated'
DATA.mkdir(exist_ok=True)


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
Regras: gere exatamente os três arquivos; use HTML sem bibliotecas externas; o index deve referenciar styles.css e app.js; inclua uma experiência bonita, funcional e mobile-first; não use explicações fora do JSON.''' 
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


def create_project(name, prompt):
    kind, theme = infer_type(prompt)
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


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)
    def end_json(self, data, status=200):
        raw=json.dumps(data,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        path=urlparse(self.path).path
        if path=='/api/health': return self.end_json({'ok':True,'service':'ForgeAI'})
        if path.startswith('/download/'):
            file=DATA / Path(path).name
            if file.exists():
                raw=file.read_bytes(); self.send_response(200); self.send_header('Content-Type','application/gzip'); self.send_header('Content-Disposition',f'attachment; filename="{file.name}"'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw); return
            return self.end_json({'error':'Projeto não encontrado'},404)
        return super().do_GET()
    def do_POST(self):
        route=urlparse(self.path).path
        if route not in ('/api/generate','/api/modify'): return self.end_json({'error':'Rota não encontrada'},404)
        try:
            length=int(self.headers.get('Content-Length','0')); body=json.loads(self.rfile.read(length) or '{}')
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
