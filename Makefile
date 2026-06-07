VENV = .venv/bin

install:
	python3 -m venv .venv
	$(VENV)/pip install -r requirements.txt

bot:
	$(VENV)/python main.py

admin:
	$(VENV)/uvicorn admin.app:app --host 127.0.0.1 --port 8080 --reload

ingest:
	$(VENV)/python -m bot.ingest --shop $(SHOP) --file $(FILE) --replace

.PHONY: install bot admin ingest
