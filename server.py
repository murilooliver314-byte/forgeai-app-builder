

# Storage migration bridge: route the existing JSON-shaped domain through
# PostgreSQL when DATABASE_URL is configured, with explicit beta fallback.
try:
    from storage import StorageBackend, storage_status
    STORAGE = StorageBackend(DATA)
    _json_load = load_json
    _json_save = atomic_save
    def load_json(path, default):
        value = STORAGE.read(path, default)
        if value is not default:
            return value
        return _json_load(path, default)
    def atomic_save(path, value):
        if not STORAGE.write(path, value):
            return _json_save(path, value)
except Exception:
    def storage_status():
        return {'backend':'json-beta','durable':False,'database_url_configured':bool(os.environ.get('DATABASE_URL','')),'migration':'adapter_error','fallback':True,'message':'Adapter indisponível; armazenamento local beta ativo.'}

_original_do_GET = Handler.do_GET
def _storage_health_get(self):
    if urlparse(self.path).path == '/api/health':
        status = storage_status()
        return self.end_json({'ok':True,'service':'ForgeAI','storage':status,'readiness':'durable' if status.get('durable') else 'beta'})
    return _original_do_GET(self)
Handler.do_GET = _storage_health_get
