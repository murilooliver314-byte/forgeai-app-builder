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


def vendacerta_files():
    html_doc='''<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>VendaCertaAI</title><link rel="stylesheet" href="styles.css"></head><body><header><div class="logo">✦ Venda<span>CertaAI</span></div><nav><button data-view="home">Início</button><button data-view="store">Minha loja</button><button data-view="assistant">Vendedora IA</button><button data-view="orders">Pedidos</button></nav><button class="outline" id="headerLogin">Entrar</button></header><main><section id="home" class="view active"><div class="hero"><small>SUA VENDEDORA PROFISSIONAL COM IA</small><h1>Venda mais.<br><em>Atenda melhor.</em></h1><p>Crie sua loja, mostre seus produtos e deixe a VendaCertaAI conversar com seus clientes.</p><div class="actions"><button class="primary" id="trialBtn">Iniciar teste grátis</button><button class="outline" id="loginBtn">Entrar</button></div><button class="adm-link" id="admBtn">ADM</button></div></section><section id="store" class="view"><div class="section-head"><div><small>LOJA PÚBLICA</small><h2>Loja da VendaCertaAI</h2><p>Produtos em destaque para seus clientes.</p></div><button class="primary" id="openChat">Falar com a vendedora</button></div><div class="products" id="products"></div></section><section id="productsView" class="view"><div class="section-head"><div><small>CATÁLOGO</small><h2>Meus produtos</h2><p>Cadastre e organize seus produtos.</p></div><button class="primary" id="addProduct">＋ Adicionar produto</button></div><div class="products" id="productsAdmin"></div></section><section id="orders" class="view"><div class="section-head"><div><small>GESTÃO</small><h2>Pedidos</h2><p>Acompanhe seus pedidos.</p></div></div><div class="orders" id="ordersList"><div class="empty">Nenhum pedido ainda. Os pedidos dos clientes aparecerão aqui.</div></div></section><section id="assistant" class="view"><div class="assistant-box"><div class="section-head"><div><small>ASSISTENTE VENDEDORA</small><h2>Conversa por texto</h2><p>A IA ajuda o cliente a escolher e comprar.</p></div><span class="status">● Online</span></div><div class="chat" id="chat"><div class="bubble ai">Olá! Sou a vendedora da sua loja. Como posso ajudar?</div></div><form id="chatForm"><input id="chatInput" placeholder="Digite sua dúvida..." autocomplete="off"><button class="primary">Enviar</button></form></div></section><section id="access" class="view"><div class="access-card"><small>MEU ACESSO</small><h2>Teste grátis ativo</h2><p>Você tem 48 horas para testar a VendaCertaAI.</p><div class="timer">47:59:59</div><button class="primary" id="codeBtn">Inserir código de acesso</button><p class="muted">Fale com Murilo pelo Instagram para adquirir acesso.</p><a href="https://www.instagram.com/geracao_ricabr?stkn=MXI0ZDlndDg1Yjk1aQ==" target="_blank">Abrir Instagram →</a></div></section><section id="admin" class="view"><div class="section-head"><div><small>CENTRAL ADM</small><h2>Painel do proprietário</h2><p>Gerencie empreendedores e códigos de acesso.</p></div><button class="primary" id="newCode">＋ Gerar código</button></div><div class="admin-grid"><div><b>Empreendedores</b><strong>1</strong><small>Conta cadastrada</small></div><div><b>Códigos disponíveis</b><strong id="codeCount">3</strong><small>Básico, Pro e Enterprise</small></div><div><b>Testes ativos</b><strong>1</strong><small>Em andamento</small></div></div><div class="code-list" id="codeList"><div><span>BÁSICO · 30 dias</span><b>DISPONÍVEL</b></div><div><span>PRO · 180 dias</span><b>DISPONÍVEL</b></div><div><span>ENTERPRISE · 365 dias</span><b>DISPONÍVEL</b></div></div></section></main><div id="modal" class="modal"><div class="modal-card"><button id="closeModal" class="close">×</button><div id="modalBody"></div></div></div><div id="toast" class="toast"></div><script src="app.js"></script></body></html>'''
    css='''*{box-sizing:border-box}body{margin:0;background:#09090f;color:#f8f7ff;font-family:Inter,Arial,sans-serif}button,input{font:inherit}button{cursor:pointer;border:0;color:inherit}.logo{font-size:20px;font-weight:800}.logo span{color:#a78bfa}header{height:70px;border-bottom:1px solid #2a2638;display:flex;align-items:center;padding:0 7%;gap:28px;background:#0d0d15}header nav{display:flex;gap:18px;margin:auto}header nav button,.adm-link{background:transparent;color:#aaa7ba;font-size:12px}.outline{background:transparent;border:1px solid #66508b;padding:10px 15px;border-radius:7px;color:#d8b4fe}.primary{background:linear-gradient(105deg,#7c3aed,#db2777);border-radius:7px;padding:11px 15px;font-weight:bold}.view{display:none;max-width:1200px;margin:auto;padding:55px 7%}.view.active{display:block}.hero{min-height:570px;display:flex;flex-direction:column;justify-content:center;background:radial-gradient(circle at 75% 35%,#68359a55,transparent 35%)}small{color:#c084fc;letter-spacing:1.5px;font-size:10px;font-weight:bold}.hero h1{font-size:clamp(42px,7vw,76px);line-height:.96;margin:17px 0;letter-spacing:-3px}.hero em{font-style:normal;background:linear-gradient(90deg,#c084fc,#f472b6);background-clip:text;color:transparent}.hero p{color:#aaa7b9;line-height:1.6;max-width:440px}.actions{display:flex;gap:10px;margin-top:22px}.adm-link{margin-top:40px;text-decoration:underline}.section-head{display:flex;align-items:end;justify-content:space-between;margin-bottom:25px}.section-head h2{font-size:29px;margin:8px 0}.section-head p,.muted{color:#908da2;font-size:12px;margin:0}.products{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.product{background:#11111a;border:1px solid #2a2638;border-radius:10px;overflow:hidden}.product-art{height:150px;background:linear-gradient(135deg,#39205d,#a052ad)}.product:nth-child(2) .product-art{background:linear-gradient(135deg,#133b58,#5bc3da)}.product:nth-child(3) .product-art{background:linear-gradient(135deg,#58203c,#ed6ca9)}.product-info{padding:14px}.product-info strong{display:block}.product-info p{color:#aaa7b9;font-size:11px}.product-info .price{color:#c084fc;font-weight:bold;margin:10px 0}.orders .empty{border:1px dashed #463859;color:#9995a8;padding:35px;text-align:center;border-radius:9px}.assistant-box,.access-card{max-width:650px;margin:auto;background:#11111a;border:1px solid #302541;border-radius:12px;padding:25px}.status{color:#4ade80;font-size:11px}.chat{height:320px;overflow:auto;background:#0c0c13;border-radius:8px;padding:15px;margin:15px 0}.bubble{max-width:80%;padding:10px;border-radius:8px;margin:8px 0;font-size:12px}.bubble.ai{background:#281b3c}.bubble.user{background:#2a2937;margin-left:auto}.chat form{display:flex;gap:8px}.chat input{flex:1;background:#0b0b12;border:1px solid #39334a;color:#fff;padding:11px;border-radius:7px;outline:0}.timer{font-size:38px;color:#c084fc;margin:22px 0}.access-card a{color:#f472b6;font-size:12px}.admin-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.admin-grid>div{background:#11111a;border:1px solid #2a2638;border-radius:9px;padding:18px}.admin-grid b,.admin-grid strong,.admin-grid small{display:block}.admin-grid strong{font-size:27px;margin:12px 0}.admin-grid small{color:#908da2;font-size:11px}.code-list{margin-top:18px}.code-list>div{display:flex;justify-content:space-between;padding:14px;border-bottom:1px solid #292438;background:#11111a}.code-list b{color:#4ade80;font-size:10px}.modal{position:fixed;inset:0;background:#000b;display:none;align-items:center;justify-content:center;padding:20px;z-index:5}.modal.open{display:flex}.modal-card{background:#171522;border:1px solid #5a3b78;border-radius:12px;padding:25px;width:min(500px,100%);position:relative}.close{position:absolute;right:14px;top:9px;background:transparent;color:#aaa7ba;font-size:25px}.modal-card h2{margin:5px 0 14px}.modal-card input{display:block;width:100%;background:#0c0c13;border:1px solid #39334a;color:#fff;padding:11px;border-radius:7px;margin:9px 0 13px}.toast{position:fixed;bottom:22px;right:22px;background:#292139;border:1px solid #7653a8;border-radius:7px;padding:12px;transform:translateY(100px);opacity:0;transition:.3s}.toast.show{transform:translateY(0);opacity:1}@media(max-width:700px){header{padding:0 16px;gap:12px}header nav{display:none}.view{padding:35px 18px}.hero{min-height:500px}.products{grid-template-columns:1fr}.section-head{display:block}.section-head button{margin-top:15px}.admin-grid{grid-template-columns:1fr}.hero h1{font-size:49px}}'''
    js='''const products=[{name:'Produto especial',desc:'Qualidade e estilo para você.',price:'R$ 49,90'},{name:'Mais vendido',desc:'O favorito dos clientes.',price:'R$ 79,90'},{name:'Novidade',desc:'Acabou de chegar na loja.',price:'R$ 99,90'}];const $=id=>document.getElementById(id);function view(id){document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));$(id).classList.add('active');window.scrollTo(0,0)}function toast(t){$('toast').textContent=t;$('toast').classList.add('show');setTimeout(()=>$('toast').classList.remove('show'),2500)}function modal(html){$('modalBody').innerHTML=html;$('modal').classList.add('open')}function close(){ $('modal').classList.remove('open') }function renderProducts(){const html=products.map((p,i)=>`<article class="product"><div class="product-art"></div><div class="product-info"><strong>${p.name}</strong><p>${p.desc}</p><div class="price">${p.price}</div><button class="primary" data-buy="${i}">Adicionar ao pedido</button></div></article>`).join('');$('products').innerHTML=html;$('productsAdmin').innerHTML=html;document.querySelectorAll('[data-buy]').forEach(b=>b.onclick=()=>{view('orders');$('ordersList').innerHTML='<div class="product"><div class="product-info"><strong>Pedido #0001</strong><p>'+products[Number(b.dataset.buy)].name+' · 1 unidade</p><div class="price">'+products[Number(b.dataset.buy)].price+'</div><button class="primary" onclick="toast(\'Pedido confirmado!\')">Confirmar pedido</button></div></div>';toast('Produto adicionado ao pedido')})}document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>view(b.dataset.view));$('trialBtn').onclick=()=>modal('<small>TESTE GRÁTIS</small><h2>Comece em 48 horas</h2><input placeholder="Seu nome"><input placeholder="Nome do negócio"><input placeholder="Seu e-mail"><button class="primary" onclick="close();view(\'store\');toast(\'Teste grátis iniciado!\')">Criar minha conta</button>');$('loginBtn').onclick=$('headerLogin').onclick=()=>modal('<small>ENTRAR</small><h2>Acesse sua conta</h2><input placeholder="E-mail"><input placeholder="Senha" type="password"><button class="primary" onclick="close();view(\'store\');toast(\'Login realizado!\')">Entrar</button>');$('admBtn').onclick=()=>view('admin');$('openChat').onclick=()=>view('assistant');$('codeBtn').onclick=()=>modal('<small>CÓDIGO DE ACESSO</small><h2>Digite seu código</h2><input placeholder="Ex.: PRO-2026-XXXX"><button class="primary" onclick="close();toast(\'Código validado! Acesso liberado.\')">Liberar acesso</button>');$('newCode').onclick=()=>{const code='PRO-'+Math.random().toString(36).slice(2,8).toUpperCase();modal('<small>NOVO CÓDIGO</small><h2>Código criado</h2><p class="muted">Entregue este código ao empreendedor:</p><div class="timer">'+code+'</div><button class="primary" onclick="close()">Fechar</button>')};$('addProduct').onclick=()=>modal('<small>NOVO PRODUTO</small><h2>Adicionar produto</h2><input placeholder="Nome do produto"><input placeholder="Preço"><button class="primary" onclick="close();toast(\'Produto salvo!\')">Salvar produto</button>');$('chatForm').onsubmit=e=>{e.preventDefault();const i=$('chatInput');if(!i.value.trim())return;const t=i.value;const c=$('chat');c.innerHTML+='<div class="bubble user">'+t+'</div><div class="bubble ai">Posso ajudar! Temos produtos em destaque e posso montar seu pedido. O que você gostaria de conhecer?</div>';i.value='';c.scrollTop=c.scrollHeight};$('closeModal').onclick=close;renderProducts();'''
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
