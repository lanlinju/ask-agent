run:
	python ask.py --agent --log-level ERROR

info:
	python ask.py --agent --log-level INFO

debug:
	python ask.py --agent --log-level DEBUG

lint:
# 	python3 -m py_compile ask.py
	pyright ask.py	

mcpserver:
	python ./server/http_server.py

pipreqs:
	pipreqs . --force --encoding=utf-8

clean:
	rm -rf ./cache
	rm -f config.json roles.json