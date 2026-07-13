PYTHON ?= .venv/bin/python
ATLAS_DEMO_ENV = \
	ATLAS_DATABASE_PATH=data/atlas-control-plane.sqlite3 \
	ATLAS_LEASE_SIGNING_KEY=atlas-demo-signing-key-change-me-00000001 \
	ATLAS_ADMIN_TOKEN=atlas-demo-admin-token-v1 \
	ATLAS_SEED_DEMO_DATA=true \
	ATLAS_MODEL_MOCK=true

.PHONY: atlas-dev atlas-worker atlas-test atlas-lint atlas-smoke atlas-reset \
	altas-dev altas-worker altas-test altas-lint altas-smoke altas-reset

atlas-dev:
	@mkdir -p data
	$(ATLAS_DEMO_ENV) $(PYTHON) -m altas serve --host 127.0.0.1 --port 8787

atlas-worker:
	$(ATLAS_DEMO_ENV) \
		ATLAS_CONTROL_PLANE_URL=http://127.0.0.1:8787 \
		ATLAS_DEVICE_ID=device_demo_local_worker \
		ATLAS_DEVICE_TOKEN=atlas-demo-device-secret-v1 \
		ATLAS_TENANT_ID=tenant_demo_fixed_ops \
		ATLAS_STORE_ID=store-sunrise-vw \
		ATLAS_AGENT_ID=agent_demo_atlas \
		$(PYTHON) -m altas worker --once

atlas-test:
	scripts/run_tests.sh tests/altas -q

atlas-lint:
	$(PYTHON) -m ruff check altas tests/altas scripts/altas-smoke.py plugins/model-providers/altas model_tools.py agent/tool_executor.py agent/agent_runtime_helpers.py
	$(PYTHON) -m ruff format --check altas tests/altas scripts/altas-smoke.py plugins/model-providers/altas
	node --check altas/control_plane/static/app.js
	git diff --check

atlas-smoke:
	$(ATLAS_DEMO_ENV) PYTHONPATH=. $(PYTHON) scripts/altas-smoke.py

atlas-reset:
	rm -f data/atlas-control-plane.sqlite3 data/atlas-control-plane.sqlite3-shm data/atlas-control-plane.sqlite3-wal

# Compatibility aliases for the prototype's original misspelled target names.
altas-dev: atlas-dev
altas-worker: atlas-worker
altas-test: atlas-test
altas-lint: atlas-lint
altas-smoke: atlas-smoke
altas-reset: atlas-reset
