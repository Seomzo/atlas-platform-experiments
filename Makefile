PYTHON ?= .venv/bin/python
ALTAS_DEMO_ENV = \
	ALTAS_DATABASE_PATH=data/altas-control-plane.sqlite3 \
	ALTAS_LEASE_SIGNING_KEY=altas-demo-signing-key-change-me-00000001 \
	ALTAS_ADMIN_TOKEN=altas-demo-admin-token-v1 \
	ALTAS_SEED_DEMO_DATA=true \
	ALTAS_MODEL_MOCK=true

.PHONY: altas-dev altas-worker altas-test altas-lint altas-smoke altas-reset

altas-dev:
	@mkdir -p data
	$(ALTAS_DEMO_ENV) $(PYTHON) -m altas serve --host 127.0.0.1 --port 8787

altas-worker:
	$(ALTAS_DEMO_ENV) \
		ALTAS_CONTROL_PLANE_URL=http://127.0.0.1:8787 \
		ALTAS_DEVICE_ID=device_demo_local_worker \
		ALTAS_DEVICE_TOKEN=altas-demo-device-secret-v1 \
		ALTAS_TENANT_ID=tenant_demo_fixed_ops \
		ALTAS_STORE_ID=store-sunrise-vw \
		ALTAS_AGENT_ID=agent_demo_altas \
		$(PYTHON) -m altas worker --once

altas-test:
	$(PYTHON) -m pytest -q tests/altas

altas-lint:
	$(PYTHON) -m ruff check altas tests/altas scripts/altas-smoke.py plugins/model-providers/altas model_tools.py agent/tool_executor.py agent/agent_runtime_helpers.py
	$(PYTHON) -m ruff format --check altas tests/altas scripts/altas-smoke.py plugins/model-providers/altas
	node --check altas/control_plane/static/app.js
	git diff --check

altas-smoke:
	$(ALTAS_DEMO_ENV) PYTHONPATH=. $(PYTHON) scripts/altas-smoke.py

altas-reset:
	rm -f data/altas-control-plane.sqlite3 data/altas-control-plane.sqlite3-shm data/altas-control-plane.sqlite3-wal
