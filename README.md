# ForgeAI — Criador de aplicativos

MVP visual e funcional do site criador de aplicativos por IA, feito sem dependências externas e pronto para abrir no navegador.

## O que já funciona

- Dashboard com métricas e projetos recentes.
- Criação de projeto por descrição em linguagem natural.
- Templates de loja, agendamento, portfólio, finanças, comunidade e curso.
- Área de projetos com busca e status.
- Editor visual com telas, preview mobile, propriedades e publicação.
- ForgeAI Studio com árvore de arquivos, editor de código, preview ao vivo e chat de alterações.
- Comandos de alteração por linguagem natural (WhatsApp, depoimentos, título e visual).
- Configurações do workspace.
- Persistência dos projetos no `localStorage` do navegador.
- Layout responsivo para celular.

## Como testar

Abra `index.html` no navegador. Não é necessário instalar nada.

## O que é MVP e o que falta para produção

O ForgeAI Studio já demonstra o núcleo da experiência Bolt/Lovable: prompt, arquivos, edição, chat de alterações e preview ao vivo. A integração com Gemini já está implementada no servidor: basta disponibilizar `GEMINI_API_KEY` no ambiente do backend. Para produção, conectar:

1. autenticação e banco de dados;
2. API de IA real para gerar e modificar código sem limites de template;
3. armazenamento de projetos e imagens;
4. sandbox seguro para executar cada projeto;
5. compilação de projetos em APK/AAB em servidor;
6. pagamentos, limites e planos;
7. publicação por subdomínio e domínio próprio.

A interface e o servidor foram preparados para essas integrações sem precisar refazer o visual.
