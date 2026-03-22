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

nvidia_nim_models:
	curl "https://integrate.api.nvidia.com/v1/models" > nvidia_nim_models.json

cp-config:
	cp -R ./agents ~/.ask-agent/agents
	cp -R ./roles ~/.ask-agent/roles
	cp -R ./skills ~/.ask-agent/skills
	cp -R ./command ~/.ask-agent/command

clean:
	rm -rf ./cache
	rm -f config.json roles.json