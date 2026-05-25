install:
	pip install -r requirements.txt
	playwright install chromium --with-deps

dev: install
	func host start

docker-build:
	docker build -t func-secure-score:latest .

docker-run:
	docker run -p 7071:80 func-secure-score:latest
