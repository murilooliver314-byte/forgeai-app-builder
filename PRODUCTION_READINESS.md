# VendaCertaAI — prontidão e armazenamento

## Implementado nesta fase

- **Abstração única de armazenamento** em `StorageBackend`: os domínios atuais (contas/sessões, catálogo, pedidos, conversas, clientes, pagamentos, auditoria, códigos e configurações) continuam usando o mesmo formato lógico, mas passam por uma porta de armazenamento comum.
- **PostgreSQL opcional via `DATABASE_URL`**: quando a variável existe, o servidor cria as tabelas `vendacertaai_kv` e `vendacertaai_migrations`, registra a versão `kv-v1` e migra os JSON legados existentes somente quando a chave ainda não existe no banco. As escritas usam `INSERT ... ON CONFLICT` e transações.
- **Fallback explícito e seguro para beta**: sem `DATABASE_URL`, ou se o PostgreSQL estiver temporariamente indisponível, o servidor usa os JSON atômicos locais com cópias de recuperação. `/api/health` informa `backend: json-beta`, `durable: false`, `readiness: beta` e não apresenta esse modo como armazenamento de produção.
- **Diagnóstico sem segredo** em `/api/health`: backend, durabilidade, versão da migração, se `DATABASE_URL` está configurada e uma mensagem operacional sanitizada. Nenhum valor de conexão é retornado.
- **Histórico de pagamentos e auditoria por tenant**: `/api/payment/history?loja=...` e `/api/audit?loja=...` exigem sessão autenticada da mesma loja e retornam somente os registros daquele tenant.
- **Status de acesso mais completo**: `/api/access/status` informa estado do trial/plano, validade, situação do plano, contagem de pagamentos e último pagamento, sem expor credenciais.
- **Loja pública preservada**: `/api/store?loja=vendacertaai` e lojas cadastradas continuam públicas; uma loja inexistente retorna 404. Produtos, pedidos, configurações, resumos, histórico e auditoria permanecem protegidos por sessão e isolamento de `slug`.
- **Dependência declarada** em `requirements.txt` (`psycopg2-binary`) para que o adapter PostgreSQL possa ser ativado no deploy, sem tornar o beta local dependente do driver.

## Estado atual e bloqueio exato

O código do adapter e a migração estão prontos, mas **a conexão PostgreSQL ainda não foi ativada neste ambiente** porque não há uma `DATABASE_URL` disponível/configurada no serviço Render. Sem essa única variável apontando para um PostgreSQL gerenciado (Render Postgres, Supabase ou equivalente), o app deliberadamente permanece em `json-beta` e não há garantia de persistência após reinício/hospedagem efêmera.

Para ativar a persistência durável, basta configurar no Render uma variável de ambiente `DATABASE_URL` com a URL privada/SSL do banco e redeployar. O primeiro boot cria o schema e importa os JSON legados sem substituir registros que já existam no banco. Depois, confirme `/api/health` com `storage.backend=postgresql`, `storage.durable=true`, `storage.migration=kv-v1-ready`.

## Outros limites que continuam explícitos

- `MERCADO_PAGO_ACCESS_TOKEN` deve ser uma credencial de produção para vendas reais. Nenhum pagamento real foi iniciado nesta fase.
- Configure `MERCADO_PAGO_WEBHOOK_SECRET` no Render para tornar obrigatória a validação de assinatura dos webhooks.
- Split/OAuth real do Mercado Pago ainda não está ativo; a comissão exibida continua sendo somente estimativa.
- Recuperação de conta, verificação de e-mail, fluxos LGPD, emissão fiscal, canais WhatsApp/Instagram e agentes autônomos avançados continuam fora desta fase.
- A vendedora IA só é real quando `GEMINI_API_KEY` está configurada; sem ela o endpoint informa indisponibilidade, sem fallback inventado.

## Vídeos & Clips (beta sem custo)

A VendaCertaAI agora tem uma seção mobile-first de vídeos verticais (`/api/videos`): clientes veem clips públicos vinculados ao produto, preço, compra e conversa com a vendedora; o empreendedor autenticado pode publicar, listar e excluir clips no painel. Cada alteração exige sessão do próprio tenant e o produto vinculado é validado no catálogo da loja.

Por segurança, o beta aceita apenas MP4/WebM/MOV, limita o arquivo a 320 KB após a leitura e limita a 100 clips por loja. A interface orienta a escolher clips curtos. Os dados passam pelo `StorageBackend` existente: com `DATABASE_URL`/PostgreSQL são persistidos no KV durável; sem banco, o fallback JSON local é explicitamente beta e pode ser perdido no Render. Nenhum Supabase Storage está configurado neste ambiente. Para produção com vídeos maiores, o próximo adaptador deve gravar o objeto em Supabase Storage (ou outro storage compatível) e manter apenas URL/metadata no KV, sem colocar base64 no catálogo.
