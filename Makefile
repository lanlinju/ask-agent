run:
	python ask.py --agent --log-level ERROR

info:
	python ask.py --agent --log-level INFO

debug:
	python ask.py --agent --log-level DEBUG

lint:
	python3 -m py_compile ask.py	

mcpserver:
	python ./server/http_server.py

pipreqs:
	pipreqs . --force --encoding=utf-8